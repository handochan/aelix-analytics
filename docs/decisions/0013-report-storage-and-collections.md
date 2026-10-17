# ADR-0013 — Report 저장·반복 묶음과 형식별 보기

작성: 2026-10-17 · 상태: Proposed — [#19](https://github.com/handochan/aelix-analytics/issues/19) 구현 전 설계 리뷰 대상.

[ADR-0006 §7·§8](0006-common-record-contract.md)의 채택된 저장·재시도 골격을 구현하기 위한 선택이다.
기존 필수 필드·저장 순서·지문 알고리즘을 유지하고, 선택 메타데이터와 묶음의 표현을 구체화한다.
이 문서의 API·필드·명령은 구현 전이다. 실제 기능은 [현재 계약](../CONTRACTS.md)을 따른다.
제품 범위는 [Report 설계](../REPORT-ARTIFACTS.md), 다른 흐름과의 순서는
[#1의 세 흐름 배정](https://github.com/handochan/aelix-analytics/issues/1#issuecomment-6232550803)이다.

## 1. 현재 코드와 구현할 경계

기준은 main `a57ee7bf7515fb6964ef8057775c7de17c76849e`(kit 0.17.0)이다.

| 이미 있는 것 | #19가 더할 것 |
|---|---|
| `writer.Writer.locked()`·`Locked.append()`의 공통 표식 검사·journal 잠금·완료 이벤트 | 복사 준비 → 잠금 안 기대 head/요청 검사 → 배타적 rename → 완료 이벤트의 조정 |
| `relations.ReadModel.report_revisions()`의 prepared/complete/incomplete·파일 해시·head 읽기 | 저장 거절과 복구가 같은 엄격한 완료 판정을 쓰도록 보강하고 묶음을 읽기 |
| `analytics.report_revision/1` schema·정상/중단/충돌 계약 fixture | 실제 writer·CLI·SDK·형식별 보기와 실행 증거 |
| `report_revision` 참조·`record_update`의 Report 상태 재생 계약 | 공개 입력에서 버전 철회·복원을 공통 상태 재생으로 처리 |

완료 이벤트가 있다는 것, 파일이 온전하다는 것, 내용이 완성됐다는 것, 결론이 검토됐다는 것은 각각의 상태다.
기존 reader가 인식하는 fixture를 실제 저장 API의 구현 증거로 쓰지 않는다.

## 2. 정체성과 보존

- Report의 논리 키는 `report_id`, 불변 주소는 `report_id + revision_id`다. 두 값은 ADR-0006의 이식 가능한 이름 규칙을 쓴다.
- 새 `revision_id`는 kit이 저장 시도마다 만드는 시간순 무작위 ID다. 충돌하면 새 ID로 배타적 디렉터리 생성을 다시 한다.
- Version n/N은 완료 이벤트 seq 순서에서 계산한 표시다. 철회·복원·재렌더는 번호나 주소를 바꾸지 않는다.
- 필수 manifest·`files[].sha256`·`entry`·`based_on`은 ADR-0006 그대로다. 작성 근거를 알지 못하면 최신 CONTEXT나 현재 데이터를 채워 넣지 않는다.
- 본문·제목·사용자 메타데이터 문자열의 Unicode·줄 끝·원본 파일 바이트를 번역하거나 정규화하지 않는다.
  보존 영역의 파일 경로는 기존 ADR의 NFC·casefold 유일성 규칙을 검사한다. 원본 파일 경로를 바꾸지 않으며,
  원본 경로와 보존 경로가 다르면 복사 계획과 응답에서 대응을 보여 준다. 유효하지 않은 목적 경로를 조용히 고치지 않는다.
- 파일 종류는 저장 허용 목록이 아니다. Markdown·HTML·PDF·슬라이드·임의 파일은 보존·다운로드할 수 있다.
  보기·변환·공유 가능한 형식은 각 adapter의 능력으로 별도 표시한다.

## 3. 선택 메타데이터와 재시도 지문

선택 입력은 `summary`·`scope`·`purpose`·`change_reason`·`generation`·`content_status`·`collection`이다.
`scope`·`generation`은 유한 JSON이고 작성자의 선언이다. `content_status`는 `partial` 또는 `complete`이며
기록 완료·분석 검사·승인 상태가 아니다. 미지정은 미지정으로 보존한다.

선택 메타데이터를 바꾼 요청을 같은 요청으로 오인하지 않으면서, ADR-0006 §8의 기존 지문 벡터를 유지한다.

1. 선택 입력이 없으면 기존 manifest·지문 입력 그대로다. 과거 파일이나 fixture를 다시 쓰지 않는다.
2. 선택 입력이 있으면 kit이 유한 JSON을 결정적으로 직렬화한 `_analytics/metadata.json`을 보존 파일 집합에 더한다.
   이 경로는 kit 전용이다. 작성자의 파일이 `_analytics/` 아래 목적 경로를 차지하면 복사 전에 `report_path_invalid`로 거절한다.
3. manifest의 선택 필드는 위 파일의 같은 값이고 `metadata_file`은 그 파일 경로다. 읽기·재시도는 두 표현의 일치를 검사한다.
   `files[]`는 이 파일의 바이트 수와 sha256을 포함한다. 그래서 기존 지문의 `{path, sha256}`가 모든 선택 입력을 결박한다.
4. `request_fingerprint`의 필수 입력·정렬·정규 문자열 규칙은 바꾸지 않는다. 선택 파일의 내용이 달라지면 같은 request ID는
   `request_conflict`다. `actor`·저장 시각·요청 ID는 기존처럼 지문에서 제외한다.

메타데이터 직렬화는 키 정렬·UTF-8·공백 없는 JSON·유한 숫자로 고정하고 별도 벡터로 검증한다.
원본 본문/첨부를 이 직렬화로 다시 쓰지 않는다. 이 파일도 Report 버전의 일부라 #20의 전체 파일 검사 대상이다.

## 4. 저장·충돌·중단·정리

저장은 명시한 파일 집합과 진입 파일을 받는다. 디렉터리 입력은 일반 파일의 전체 목록으로 펼친 뒤 같은 경로 검사를 한다.
심볼릭 링크·junction·일반 파일이 아닌 항목은 추적하지 않고 오류로 돌려준다. 복사한 바이트가 보존의 기준이며,
복사 전후 파일 목록·정체성·크기/시각·해시가 달라진 관측은 `report_source_changed`로 실패한다.
이 관측이 동시 편집된 파일 전체의 원자적 스냅샷을 증명한다고 표시하지 않는다.

순서는 ADR-0006 §7의 한 번의 journal 잠금을 유지한다.

1. 표식과 요청·목적 경로를 검사한다. 잘못된 입력은 staging을 만들지 않는다.
2. 새 시도 전용 잠금을 잡고 `.staging/<revision_id>/content/`에 복사·해시·manifest를 준비한다.
3. `Writer.locked("save_report")` 안에서 기록·보존 파일·같은 request ID·기대 `previous_revision`을 다시 읽는다.
   head는 유일하고 유효한 완료 이벤트가 있는 마지막 버전이다. 모호한 ID·손상된 해당 Report의 완료 기록은 임의의 head로 고르지 않는다.
4. 같은 ID/지문/완료 버전이면 `existing`; 다른 지문이면 `request_conflict`다.
   같은 지문인 rename 후 미완료 버전은 파일과 manifest를 검사하고 head가 그 `previous_revision`일 때만 `resumed`로 완료한다.
   아니면 `revision_conflict`이고 기존 미완료 버전은 보존한다.
5. 새 저장은 기대 head가 맞을 때에만 플랫폼의 `rename_new`로 staging을 불변 버전 경로에 옮긴다.
   새 쓰기 규칙의 kit 버전 floor를 첫 보존 변경 전에 올리고, manifest digest를 가진 `report_revision` 완료 이벤트를 덧붙인다.
6. 예외나 응답 손실은 실제 디렉터리·이벤트 상태로 다시 읽는다. prepared와 rename 후 incomplete를 성공으로 바꾸지 않는다.
   호출자가 자동 재생성을 원한다고 해석하지 않는다. 같은 request ID로 저장을 재시도한다.

잠금 순서는 시도 잠금 → journal 잠금이다. 시도 잠금은 `.analytics/report-attempts/<revision_id>.lock`에 있고
복사 시작부터 완료/실패 처분까지 유지한다. 보존 파일 안에 lock을 두거나 lock 파일을 자동 삭제하지 않는다.
명시적인 `reports clean --prepared <report_id>@<revision_id>`는 같은 시도 잠금과 journal 잠금 순서로 검사하고,
**지정한 staging 하나만** 아직 prepared일 때 지운다. 기다리는 동안 저장된 버전·다른 시도·incomplete는 지우지 않는다.
일반 조회·렌더·캐시 정리는 준비 중인 파일도 삭제하지 않는다. 실행 중 시도를 오래됐다는 이유로 인수하지 않는다.

현재 `Writer.update`의 공개 target은 이벤트 ID 중심이다. Report의 철회/복원 입력은 정확한 ReportRef를 받아
journal 잠금 안에서 같은 상태 재생 규칙으로 `record_update`를 쓰도록 확장한다. prepared/incomplete는 철회 가능한 버전이 아니다.

## 5. 반복 보고서 묶음

별도 이벤트 kind나 schema 가족을 만들지 않는다. 묶음도 하나의 Report이고, `collection` 선택 필드가 있는
`analytics.report_revision/1`의 각 버전이 묶음 스냅샷이다. `collection_id = report_id`, 묶음 버전 주소는 `revision_id`다.
본문은 자유 형식이고 시스템이 모든 구성원을 성공이라고 요약한 문서를 강제하지 않는다.

`collection`은 `contract: report_collection/1`과 요청한 전체 구성원의 순서 있는 `members`를 가진다.

| 구성원 필드 | 의미 |
|---|---|
| `member_key` | 컬럼·대상·기간을 구별하는 작성자 키. 문자열을 그대로 보존하고 한 스냅샷 안에서 중복하지 않음 |
| `scope` | 선택: 요청한 대상·필터·정렬·인자 등의 유한 JSON. 이름만으로 실제 데이터 관측을 추정하지 않음 |
| `status` | `complete`, `failed`, `not_generated` |
| `report_id`, `revision_id` | `complete`일 때 필수인 정확한 구성원 버전. 나머지 상태에는 없음 |
| `reason` | 실패 또는 미생성일 때 필수인 작성자 설명 |

저장 시 complete 구성원은 이 프로젝트의 이전 완료 이벤트와 온전한 보존 파일이 있어야 한다.
하나의 구성원 버전은 그 바이트를 가리킬 뿐 내용의 완전성이나 검토를 보증하지 않는다.
집계 requested/complete/failed/not_generated는 `members`에서 계산한다. 임의로 받은 성공 수로 대체하지 않는다.
이전 구성원의 새 버전이나 실패분 추가는 **새 묶음 버전**이다. 과거 묶음의 참조·바이트·부분 상태를 고치지 않는다.
collection의 구조 참조는 `includes`, `basis=declared`, `version_binding=author`로 읽는다.
작성 근거 `based_on.refs`와는 구분하며, 문서 전체의 확정 근거나 결론 검토 수를 늘리지 않는다.
저장 순서상 구성원이 묶음보다 먼저 완료돼야 하므로 새 묶음 저장은 자기 참조·미래 참조를 받지 않는다.

## 6. 공통 입력과 읽기

| 표면(제안) | 역할 |
|---|---|
| `analytics reports save <report_id> --entry <path> --files <dir> --title <text> --previous <revision_id|none> [--request-id <id>] [--metadata <json-file>] [--based-on <json-file>]` | 명시한 입력을 보존. 같은 명령이 외부 작성 파일 등록도 맡음 |
| `analytics reports list`, `show <report_id>@<revision_id>` | 파일·상태·근거·이력·묶음 조회. 목록은 본문 전체를 읽지 않음 |
| `analytics reports update <report_id>@<revision_id> --expected-state <state> --state <state> --reason <text>` | 정확한 버전의 철회/복원 |
| `analytics reports clean --prepared <report_id>@<revision_id>` | §4의 명시적 prepared 정리 |
| `analytics.save_report(...)`, `analytics.reports(...)` | 동일 응용 기능의 SDK. CLI와 같은 충돌·상태·참조 결과 |

각 명령의 POSIX `bin/analytics`와 설치 런처, kit CLI는 인자·응답·종료 코드가 같다.
`--previous`는 첫 저장도 `none`으로 명시해, 기존 Report를 자동 최신 head 위에 덮어쓰지 않는다.
SDK도 previous를 명시한다. request ID를 생략하면 kit이 만들어 응답한다.
등록은 생성 코드를 실행하지 않는다. 작성 주체는 기존 `Origin`으로 기록하고 외부 작성 출처는 미확인/선언으로 남긴다.
에이전트는 기존 실행 경로로 같은 CLI/SDK를 부르며 전용 상시 모델 도구를 더하지 않는다.
bounded 조회는 metadata/파일 목록/근거부터 시작하고 본문은 선택한 파일·범위만 읽으며 이어 읽을 방법을 돌려준다.
모르는 형식과 본문 없음은 다른 상태다. 읽기·등록·재시도·render에 추가 모델 호출이나 원래 분석 실행을 넣지 않는다.

## 7. ReportRenderer와 Workspace·공유

`reports.py` 응용 기능은 주입받은 Writer·플랫폼과 읽기 모델로 저장을 조정한다. read_model은 이 모듈을 import하지 않는다.
형식별 보기 자료형과 `ReportRenderer` Protocol은 별도 낮은 계층 모듈에 두고, 진입점이 구현을 주입한다.
새 모듈을 더하는 구현 PR에서 `tests/test_layers.py`·아키텍처 §11을 함께 갱신한다.

`ReportRenderer`의 입력은 검증된 ReportRef·manifest·선택 파일 바이트와 렌더 대상의 상대 링크 매핑이다.
출력은 `{path: bytes}`인 재생성 보기 파일과 원본 파일 링크·지원/미지원 사유다. renderer는 프로젝트 폴더,
현재 데이터·최신 정의·모델·네트워크·생성 실행을 직접 조회하지 않는다. #20은 공유 범위를 적용한 입력만 준다.

Markdown은 원시 HTML 실행 없이 보기, HTML은 원본 보존과 별도로 script·원격 자원을 실행하지 않는 격리 보기 또는
정적 미리보기/다운로드를 제공한다. 임의 HTML을 Workspace DOM에 삽입하지 않는다. PDF·Office·미지원 형식은
다운로드와 metadata가 기본이다. 원본을 보기용으로 고쳐 쓰거나 지원하지 않는 오프라인 열람을 성공으로 표시하지 않는다.
#20의 형식 검사·제외 내용 검사·`report_not_shareable`을 우회하는 옵션을 이 renderer에 넣지 않는다.

Reports 탭은 #99 병합 뒤 공통 버전 n/N·시각·이력·최신 안내·접힌 묶음을 사용한다.
정확한 버전에서 당시 근거·첨부·코드로 갔다가 같은 보고서 위치로 돌아오며 기존 #18 URL 상태·돌아가기 규칙을 유지한다.
kit 문구는 #96 카탈로그의 English/한국어를 따르고 사용자 내용은 원문 그대로다. 상태·실패 이유·구성원 목록은 JS 없이도 읽힌다.
#100도 Workspace를 바꾸므로 마지막 PR의 공유 파일 작업과 병합 시점을 흐름 A와 조율한다.

## 8. PR 경계와 검증

| PR | 산출물·착수 조건 | 수용 증거 |
|---|---|---|
| PR-0 설계 | 이 ADR, 채택 골격과의 연결, #20·#99 경계. runtime/schema 변경 없음 | 독립 설계 1회·별도 Codex 설계 1회, 문서 검사. 아직 구현 통과 아님 |
| PR-1 저장·등록·버전 | PR-0 결정 뒤. 실제 writer·공통 응용 기능·CLI/SDK·schema·엄격한 reader·철회/복원·정리 | 독립 구현 1회·별도 Codex 1회와 수정 diff 확인, 세 OS CI, 기록 형식 측정 md+JSON |
| PR-2 반복 묶음·보기 | PR-1 뒤. 선택 metadata·collection·ReportRenderer·원본 다운로드 | 같은 기록 형식 리뷰·측정, 부분 성공/실패분 추가·renderer 교체 계약 시험 |
| PR-3 Reports 탐색 | PR-2와 #99 병합 뒤. #100 작성자와 공유 파일 조정 | 일반 PR 리뷰 예산, S/M 비용 비교, 데스크톱/모바일·두 언어·키보드·JS 없음 화면 결과를 실행 주체별 기록 |

PR-1은 다음 실패 지점마다 디렉터리·journal·head·request 결과를 실제로 검증한다:
복사 중단, manifest 쓰기 중단, rename 전/후, 완료 이벤트 실패/끊긴 줄, 응답 손실, 동시 같은 이전 버전 저장,
같은 요청/다른 지문, 같은 요청/같은 지문, 재개 중 head 변경, marker floor 경합, 파일 누락/변조와 prepared 정리 경합.
한 writer의 request 중복 범위는 `report_id` 하나이고, 다른 Report의 같은 request ID는 독립이다.

PR-2는 v2·재렌더 뒤 v1 바이트/근거/묶음 불변, 20개 중 18개 성공과 실패/미생성의 구별, 실패분 추가 뒤 옛 묶음 유지,
Unicode 내용·경로·JSON metadata, 모든 선택 필드 변경의 지문 차이, 알 수 없는 선택 필드 보존을 검증한다.
기존 ADR-0006 지문 벡터·records fixture는 별도로 유지한다.
schema·fixture·공개 계약은 실제 구현 PR과 함께 바꾸며, 비용은 생성/저장/조회/렌더를 구별한다.
kit 버전·CHANGELOG·uv.lock은 먼저 병합된 #20·흐름 A 뒤의 다음 번호를 받는다.

## 9. 한계와 후속 작업

이 저장 절차는 프로젝트 전체 트랜잭션·외부 생성 코드의 정확히 한 번 실행·악의적인 외부 파일 변경에 대한 무결성 인증을 제공하지 않는다.
목록을 다시 그렸다고 결론이 검토되거나 원본 데이터가 보존되는 것도 아니다.
문서 변환기 전체·시각 편집기·예약 생성·공동 편집·자동 prepared 청소는 이번 범위 밖이다.
실제 모델/사람 인계 Q17과 실제 다른 독자의 공유 열람은 후속 #23·#24에서 판단한다.
