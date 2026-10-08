# 용어·명칭·기본 언어의 저장소 점검

작성: 2026-09-18 · 상태: 읽기 전용 코드 조사와 설계 반영. 실행 코드·저장 형식·기본 템플릿의 전환은 아직 수행하지 않았다.
현재 구현은 `21b6a3c` 계열의 소스와 작업 중 설계 문서를 확인했다. 최종 용어 기준은 [용어집](TERMINOLOGY-AND-LANGUAGE.md)이다.

## 1. 확인 범위

`lab`·`report` 검색 외에 Python의 공개 함수·클래스·상수·문자열·도구 인자·레코드 필드·상태,
CLI·환경 변수·JS/CSS·기본 템플릿·데모·시험과 모든 설계 문서의 명칭을 확인했다.
대응은 유지·명칭 변경·의미 구분·legacy 보존으로 나눈다. 모든 내부 지역 변수를 새 추상 용어로 바꾸지는 않는다.

| 확인 영역 | 현재 위치 | 확인한 문제와 결정 |
|---|---|---|
| 배포·실행 진입점 | [pyproject.toml](../pyproject.toml), [bin/lab](../bin/analytics), [CLI](../src/aelix_analytics/__main__.py) | 배포 이름은 이미 Analytics지만 CLI·module·help는 lab. 단일 `analytics` 표면으로 전환 |
| Aelix 연동 | [aelix_lab.py](../extension/aelix_analytics.py) | flag·명령·6 tools·환경 변수·help·복구 hint·하위 프로세스·telemetry 분류의 이름을 함께 변경 |
| SDK·관용 코드 캡처 | [SDK](../src/aelix_analytics/__init__.py), [capture.py](../src/aelix_analytics/capture.py) | `labkit as lab`, `lab.note`, `lab.chart`, 관측 `via`·backend·file format·style 설정 점검. record/visualize 및 Visualization 상위 객체 채택 |
| 프로젝트·원본 기록 | [core.py](../src/aelix_analytics/core.py) | marker·state·schema·trust·event kinds·refs·context·status·캐시·읽기 정책 전환 필요 |
| 실행·재실행 | [runner.py](../src/aelix_analytics/runner.py) | run·입출력·환경·기록 완료·비교 결과의 구별. `reproduction`을 Rerun comparison으로 명시 |
| 데이터 | [data.py](../src/aelix_analytics/data.py) | imported/source/derived·dataset profile·SQL view·table schema·preview를 구별 |
| 인터페이스와 링크 | [report.py](../src/aelix_analytics/workspace.py), [report.js](../src/aelix_analytics/assets/workspace.js), [report.css](../src/aelix_analytics/assets/workspace.css) | Workspace와 Report 분리. 시각화 객체·탭·property·오류·aria label·페이지·자원·anchor·갱신 stamp까지 전환 |
| 기본 파일 | [CONTEXT](../src/aelix_analytics/template/CONTEXT.md), [project template](../src/aelix_analytics/template/pyproject.toml), [gitignore](../src/aelix_analytics/template/gitignore) | 영어 기본 안내·섹션·placeholder, analytics 프로젝트 이름, 올바른 파생/보존 경로 규칙 |
| 데모·예시 | [demo.py](../src/aelix_analytics/demo.py), [예제 스크립트](../src/aelix_analytics/demo_scripts/) | 첫 경험의 제목·축·범례·설명·기록·명령을 영어 기본 예제로 정리. 한국어 사례는 다국어 검증으로 유지 |
| 시험 | [fixtures](../tests/conftest.py), [kit tests](../tests/test_kit.py), [extension tests](../tests/test_extension.py) | helpers·환경·경로·expected labels·schema·SDK 예제를 전환. 과거 형식·한국어 회귀 사례는 별도로 유지 |
| 제품·계약·조사·설계 | [README](../README.md), [기여 가이드](../CONTRIBUTING.md), `docs/` 전체 | 미래 설계는 새 용어로 통일. 현재 계약과 과거 평가·세션·외부 레퍼런스는 당시 이름 보존 |

외부 Analytics Engine의 `View`, `SelectionRef`, `Claim` 등은 그 저장소의 계약명이다. 이 저장소의 전역 이름 변경으로 외부 계약까지 바꿨다고 표시하지 않는다.

## 2. 발견한 의미 충돌

| 현재 표현 | 확인한 실제 의미 | 전환 또는 구분 |
|---|---|---|
| report·report build | 프로젝트 전체 HTML 탐색과 갱신 | Workspace·render; 별도 문서 산출물에 Report 사용 |
| chart·charts | 그림 라이브러리·HTML·이미지 파일을 담는 분석 표현 | 상위 객체 Visualization, 통계 차트는 Chart 유형 |
| `view`, `views` | DuckDB에 등록한 조회 이름 | SQL view·`sql_views`; 화면 View와 구별 |
| `data/raw` | 파일을 복사하거나 Parquet로 변환하고 일부 타입을 조정해 가져옴 | Imported data·`data/imported`; 원본 Source file을 따로 참조 |
| chart `data`, `has_data` | 사용자가 연결한 첨부 표 | Attached data. 실제 플롯과 완전 대응함은 별도 검사 |
| `reproduction.verdict=match` | 비교 가능한 결과의 선택된 비교가 일치 | Rerun comparison·Matched. 환경 재현·인과·결론 정확성 인증이 아님 |
| `retired` | 같은 스크립트의 최신 성공 run에서 그림이 빠짐 | Absent from latest run; 삭제·철회와 구별 |
| `stale` | 관측한 입력 변경·누락과 상류 영향 | Input changes·확인 시각; 결론의 유효성 판정과 구별 |
| `gap`, 평가 | 분석 도구 사용 중 문제와 기록·도구 통계 | Workflow feedback / Insights / Record coverage / Tool usage |
| `revision` | 설계에서 상태 변경 이벤트와 내용 버전 두 뜻 | 이벤트 `record_update`, 내용 `revision_id` |
| `always`, `always_on` | CONTEXT pin과 skill 자동 제안 모드 | 새 template `pin`, 모드 값은 유지하며 UI Automatic suggestions |
| 예산·budget | 현재의 실제 문자 절단과 미래 설계의 성능 목표가 혼재 | 목표와 실제 제한을 구별. 개명만으로 현재 절단 동작이 고쳐졌다고 하지 않음 |
| `schema`, `kind`, `profile`, `source`, `version` | 객체에 따라 다른 뜻 | record/table schema, event kind, visualization type/backend, dataset profile, source file/revision 등 명시 |
| bytes·시간 표시 | `fmt_bytes`는 1024 단위 계산에 KB/MB, `fmt_time`은 문자열 일부 표시 | 단위 label·시간대 정보를 계산 의미와 맞추는 별도 표시 수정 |

현재 차트 레코드의 `kind`는 단순 bar/line 유형을 뜻하지 않는다. 생성 backend·보존 형식과 시각화 유형은 다른 속성이다.
기존 `Claim`·‘verified’ 용어도 실제 검사 범위 없이 더 강한 의미로 승격하지 않는다.

## 3. 도구·SDK·필드의 이행 목록

| 현재 도구 | 주요 인자 | 새 표면에서의 의미 |
|---|---|---|
| `lab_status` | 없음 | `analytics_status`: 프로젝트 현황, 설계된 부분 조회는 별도 계약으로 추가 |
| `lab_query` | `sql`, `limit` | `analytics_query`: 데이터 SQL 조회. 프로젝트 지식 검색과 혼동하지 않음 |
| `lab_import` | `source`, `name`, `description` | `analytics_import`: 외부 source를 dataset으로 가져오기 |
| `lab_run` | `title`, `script`, `code`, `name`, `rerun_of`, `inputs`, `timeout_s`, `attach_images` | `analytics_run`: 자유 코드 실행. `name`은 inline script 이름으로 범위를 명시하고, 이미지 첨부와 Visualization 등록을 구별 |
| `lab_note` | `kind`, `text`, `refs`, `resolves` | `analytics_record`: typed journal entry. 일반 note는 그중 한 kind |
| `lab_init` | `path`, `title`, `goal` | `analytics_init`: Project 생성 |

SDK는 `read/scan/sql/save/datasets/project/path/run_id`를 유지하고 `note → record`, `chart → visualize`로 정리한다.
사람용 CLI의 `init/demo/status/context/run/import/query/open`은 뜻을 유지한다. `report → render`, `note → record`로 정리한다.
`where`·`use`는 CLI 하위 명령이 아닌 Aelix의 `/lab where`·`/lab use`이며 `/analytics` 아래에서도 그 역할을 유지한다. `/lab gap`의 편의 경로는 `record workflow_feedback`으로 정리한다.
동일한 기능을 옛 이름·새 이름으로 기본 노출하지 않는다. 인자 의미·성공/실패·대응하는 오류 안내를 함께 비교한다.

기록 전환 시 추가 점검할 필드 묶음:

- **정체성·시간:** schema, id, seq, ts, created_at, started_at, ended_at, imported_at, actor, session. 사건 순서와 적용 기간을 구별한다.
- **버전·참조:** refs, runs, charts, data, events, context, other, resolves, script, modules, snapshot, sha256, source_revision. 논리적 이름·정확한 버전·파일 위치를 구별한다.
- **데이터:** name, path, rows, columns, dtype/type, bytes, source, format, modified_at, schema, profile, preview. 원본 컬럼과 library type 문자열은 임의로 영문화하지 않는다.
- **실행·관측:** inputs, outputs, change, external, via, outside_data, status, error, finalize_errors, warnings, stdout, stderr, env, packages, uv_lock_sha256, kit_version/kit_sha256, rerun_of, reproduction. `kit_*`는 새 런타임 지문 이름으로 명확히 한다.
- **시각화:** charts, id/key, kind, via, title, caption, files, data, data_rows, data_columns, line, versions, retired, has_data. 새 객체·경로·field 변환을 함께 명시한다.
- **조회·UI:** report, build, pages, counts, key/open/recent, views, unreadable, context_omitted/context_truncated, truncated, limit, tables, elapsed_s/elapsed_ms. `key`는 현재 발견·결정·가정 묶음이라는 뜻을 밝혀 단일 확정 사실 목록처럼 표시하지 않는다.
- **로그·측정:** telemetry의 tool/ms/error/summary/flags/session, queries의 ts/sql/rows/tables/error. 도구 시간·실행 시간·모델 요청·토큰을 서로 다른 단위로 유지한다.

새 field 이름은 이 목록의 뜻을 기준으로 schema PR에서 확정한다. `kind`, `name`처럼 객체 안에서 이미 분명한 키를 모두 길게 확장할 필요는 없다.
새 필드가 모든 이전 기록에 관측되어 있었다고 가정하지 않고, reader가 채운 값은 변환·미확인 여부를 드러낸다.

## 4. 영어 기본값의 적용 지점

현재 확장 도구 설명·CLI는 대체로 영어지만 Workspace 본문·JS 탭·오류·aria label, CONTEXT 기본 파일, 데모의 제목·그림 설명에는 한국어가 있다.
탭만 바꾸어 영어 전환을 완료 처리하지 않는다. 상태 badge·빈 목록·tooltip·다운로드 안내·생성 시각·footer·오류 hint도 같은 기준을 적용한다.

템플릿의 `## 목표`와 `<!-- 항상 -->`는 실제 parser와 연결되어 있다. 새 영어 템플릿·`pin` 구문·parser를 함께 바꾸고 기존 한국어 문서도 읽는지 확인한다.
한국어 대화에서 작성한 Goal·Finding·Visualization title·Report body는 그대로 유지한다. 영어 UI label만 보고 본문을 번역하지 않는다.
데모의 영어 기본 사례와 한국어·Unicode 경로·열·본문을 가진 회귀 사례를 모두 둔다.
언어별 label catalog는 하나의 의미 key를 공유하며, 번역 누락 시 영어 fallback을 사용한다. 분석 내용을 번역하기 위한 숨은 모델 호출을 추가하지 않는다.

## 5. 전환의 경계

초기 공개 전 정리는 이름을 통일할 기회지만, 이미 사용한 프로젝트·실행 증거는 보존한다.
기존 설계의 옛 진입점 영구 유지안 대신 **새 공개 표면 하나와 명시적인 legacy 읽기·전환**을 채택한다.

1. 새 프로젝트·예제·도구에는 Analytics/Workspace/Visualization/Report와 영어 기본값을 사용한다.
2. 기존 프로젝트의 reader는 지원하는 namespace·버전을 명시적으로 분기한다. 알 수 없는 schema 가족을 정상 기록처럼 읽거나 조용히 생략하지 않는다.
3. **namespace만 바꿔서는 구버전 쓰기가 차단되지 않는다.** 현재 `newer_schema`는 같은 가족의 숫자만 비교하므로 marker·writer·전환을 함께 검증한다.
4. 기존 프로젝트는 원본 보존 후 별도 목적지에 복사해 전환하는 경로를 기본 후보로 한다. 원본/목적지와 참조 매핑을 기록하고, 조용한 in-place 이름 변경·이중 쓰기를 하지 않는다.
5. 기존 journal 줄·run JSON·실행 코드·이미지·보고서 바이트와 해시는 그대로 보존한다. 새 읽기 모델에서 경로·이름을 대응시키며 변경한 작업 파일은 별도 diff로 남긴다.
6. 과거 스냅샷을 재실행할 때 필요한 구버전 SDK·환경을 명시한다. 당시 코드를 새 SDK로 바꿔 실행했다면 원래 바이트의 rerun이라고 표시하지 않는다.
7. `.lab/` 안의 context 이력·로그·상태와 cache를 구별한다. `.analytics/` 전환·캐시 청소·gitignore가 원본이나 새 Report 산출물을 지우지 않게 한다.
8. `data/raw → data/imported`, `runs/*/charts → visualizations`, `report → workspace`는 기록된 경로·참조·링크·해시의 대응을 포함한다. 단순 디렉터리 이동만으로 완료하지 않는다.
9. 구·신 marker 또는 설정이 충돌하면 정해진 오류와 해결 경로를 제공한다. 신뢰 목록 이행으로 이전에 선택하지 않은 경로를 자동 신뢰하지 않는다.
10. 원격 저장소·사용자의 demo 프로젝트·호스트 세션 파일은 이 문서 작업으로 변경하지 않는다. 실제 전환은 검증된 프로젝트 변환 기능으로 수행한다.

새 schema·필드 의미·이벤트 종류는 B1-c에서 확정하고, 단순 코드명·언어 전환은 별도 PR로 검토할 수 있게 나눈다.
compatibility adapter가 필요해도 모델 도구에 옛 별칭을 다시 노출하지 않는다. 지원 범위와 제거 조건을 기록한다.

## 6. 구현·검증 묶음

| 묶음 | 변경 범위 | 완료 증거 |
|---|---|---|
| N0-a 표면·책임 이름 | CLI·SDK·도구·flag·환경·오류·module, Workspace/Visualization 구분 | 새 이름만 노출하는 여섯 도구, 모든 help·예제·하위 프로세스 호출 일치, 기존 자유 코드 흐름의 동등성 |
| N0-b 저장·legacy | marker·namespace·refs·paths·reader·전환. [#13](https://github.com/handochan/aelix-analytics/issues/13)에서 B1-c와 함께 한다([ADR-0005](decisions/0005-names-first-storage-with-the-new-writer.md)) | 구버전 fixture 읽기·새 프로젝트 쓰기·미지원 schema·두 marker 충돌·부분 전환·원본 해시 보존·옛 일반 writer 차단 |
| N0-c 영어 기본값 | template·Workspace label·데모·parser·문자열 카탈로그 | 새 기본 프로젝트와 데모의 영어 UI, 한국어 내용·기존 CONTEXT·Unicode 경로 보존, 모든 주요 화면·오류·접근성 label 확인 |
| B1/B4 연결 | 새 도메인 field·상태·보고서·Visualization 참조·공유 | 같은 의미 key와 ID를 사람·에이전트가 사용, 보고서·근거·과거 버전으로 이동, 추가 상시 도구 없음 |

N0는 A0와 함께 공개 경계를 정리하되, 새 기록의 의미를 추가하는 B1과 대규모 이름 치환을 한 번에 섞지 않는다.
현재 이름을 의도적으로 남긴 곳은 legacy reader·fixture·migration 안내·과거 증거로 분류한다. 단순히 저장소 검색 결과가 0건인 것을 성공 기준으로 삼지 않는다.
영어 기본화도 한국어 문자열 0건이 목표가 아니다. 시스템 소유 label의 영어 기본값과 다국어 사용자 내용 보존을 함께 검증한다.

명칭 변경만으로 기능·토큰 회귀를 가릴 수 없으므로 도구 수·schema tokens·인자 오류·과제 완수·core 의존성을 같은 조건에서 비교한다.
이 기준은 [베타 게이트](BETA-QUALITY-GATES.md)의 Q1·Q4·Q6·Q7·Q10·Q11·Q13·Q15·Q16에 연결한다. 아직 구현·검증을 통과했다는 뜻은 아니다.
