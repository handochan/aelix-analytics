# 제품 용어·코드 명칭·기본 언어

작성: 2026-09-18 · 상태: 일부 구현. §2의 명령·하위 명령·도구·flag·SDK·확장 파일·모듈과 §3의 환경 변수는 #9(kit 0.2.0·0.3.0,
2026-10-06)가 적용한 현재 명칭이다(옛 이름은 별칭 없이 새 이름을 알리는 오류). §2의 오류·내부 상태·표시 구현·Workspace 빌드 결과·
Workspace 내부 식별자는 [#13](https://github.com/handochan/aelix-analytics/issues/13)의 첫 PR(⓪, kit 0.6.0)이, §2의 기록 writer와 §6의
표식 판정·작성 출처의 이름은 첫 구현 PR(①, kit 0.7.0)이, §2의 전환 엔진과 §6의 프로젝트 전환의 이름은 둘째 구현 PR(②, kit 0.7.0, 명령으로는
열지 않음)이, §2의 공통 writer와 §3의 저장 이름(표식·관리 디렉터리·화면·가져온 데이터·Visualization 디렉터리·schema namespace·신뢰 목록·`via`)은
셋째 구현 PR(③, kit 0.8.0)이 새 쓰기에 적용한 현재 명칭이다. 옛 저장 이름의 기록(`lab.json` 프로젝트, 전환한 목적지에 복사한 옛 파일)은
이름을 바꾸지 않고 읽기만 한다(#13).
§7의 영어 기본값은 [#10](https://github.com/handochan/aelix-analytics/issues/10)(2026-10-06)이 Workspace·템플릿·데모에 적용했다. 현재 상태는 §7 끝의
'현재 구현'에 있다.
[현재 계약](CONTRACTS.md)은 구현을, [전체 용어 점검](REVIEW-TERMINOLOGY-2026-09-18.md)은 확인한 위치·변경 범위·전환 순서를 설명한다.
이 문서는 앞선 설계의 `lab_*` 영구 호환·프로젝트 화면을 `report`로 부르는 안을 대체한다. 과거 기록의 보존 원칙은 유지한다.

## 1. 이름의 기준

**제품·명령은 Analytics, 분석 프로젝트는 Project, 분석 인터페이스는 Workspace, 작성한 보고서는 Report로 구분한다.**
`Analysis`는 분석 활동·목적·초점을 뜻하는 일반 용어로 사용한다. `analytics`와 `analysis`를 같은 명령의 두 이름으로 제공하지 않는다.
Workspace는 프로젝트를 탐색하는 인터페이스이며 프로젝트 루트·데이터 저장소의 동의어로 쓰지 않는다.
현재는 정적 HTML로 구현된 인터페이스다. 이 명칭만으로 브라우저에서 코드 실행·공동 편집이 가능하다고 안내하지 않는다.

새 명칭은 기능·데이터 수명·관측 범위를 설명해야 한다. 같은 뜻에 동의어를 늘리거나 기존 이름을 무조건 길게 만드는 것이 목표가 아니다.
`run`, `dataset`, `query`, `journal`, `context`처럼 뜻이 맞는 단어는 유지한다. 시각화 상위 객체는 `Visualization`, 그 안의 통계 차트는 `Chart`로 구분한다.
공개 이름은 한 가지를 사용하고, 영어 UI label과 안정된 기계 식별자를 분리한다.

## 2. 제품·명령·패키지의 대응

| 대상 | 현재 명칭 | 공식 전환 목표 |
|---|---|---|
| 제품 / 저장소·배포 이름 | Aelix Analytics / `aelix-analytics` | 유지 |
| Aelix 사람 명령 | `/analytics`(kit 0.3.0부터. 그 전은 `/lab`) | `/analytics` |
| CLI / 모듈 실행 | 체크아웃의 `bin/analytics`와 설치본의 `analytics`(kit 0.3.0부터. 그 전은 `bin/lab`), `python -m aelix_analytics`(실행 루트에서만, 2026-10-06부터. 그 전은 `python -m labkit`) | `analytics`, `python -m aelix_analytics` |
| Python SDK | `import aelix_analytics as analytics`(2026-10-06부터. 그 전은 `import labkit as lab`, 별칭 없음) | `import aelix_analytics as analytics` |
| 확장 진입 파일 | `extension/aelix_analytics.py`, 설치본의 `aelix_analytics.extension`(kit 0.3.0부터. 그 전은 `extension/aelix_lab.py`) | `extension/aelix_analytics.py`; 패키징 이후 `aelix_analytics.extension` |
| 프로젝트 flag | `--analytics-project`(kit 0.3.0부터. 그 전은 `--lab-project`) | `--analytics-project`; CLI 내부 `--project`는 유지 |
| 모델 읽기·SQL·가져오기 | `analytics_status`, `analytics_query`, `analytics_import`(kit 0.3.0부터. 그 전은 `lab_*`) | `analytics_status`, `analytics_query`, `analytics_import` |
| 모델 실행·기록·초기화 | `analytics_run`, `analytics_record`, `analytics_init`(kit 0.3.0부터. 그 전은 `lab_run`, `lab_note`, `lab_init`) | `analytics_run`, `analytics_record`, `analytics_init` |
| 사람/SDK의 기록 쓰기 | `analytics record`, `analytics.record(...)`(kit 0.3.0부터. 그 전은 `lab note`, `note(...)`) | `analytics record`, `analytics.record(...)` |
| SDK의 시각화 기록 | `analytics.visualize(...)`(kit 0.3.0부터. 그 전은 `chart(...)`). 기록의 `via`는 kit 0.8.0부터 `analytics.visualize`(그 전 `lab.chart`, §3 `via` 행) | `analytics.visualize(...)`; 객체와 참조는 `visualization` |
| 프로젝트 화면 생성·열기 | `analytics render`, `analytics open`(kit 0.3.0부터. 그 전은 `lab report`, `lab open`) | `analytics render`, `analytics open` |
| 프로젝트 전환 명령 | `analytics migrate --to DIR [--dry-run]`(kit 0.8.0부터, #13), kit의 `python -m aelix_analytics [--project SRC] migrate --to DIR [--dry-run]`. 모델 도구와 `/analytics migrate`는 없음(사람이 터미널에서, [설계](design/RECORD-WRITER-AND-MIGRATION.md) 6.1절) | 유지 |
| 공유 범위 미리보기 | `analytics build --preview`, `/analytics build --preview`(kit 0.23.0). read/evidence와 메모리 검사 요약만, 기록·폴더·환경 생성 없음 | 저장 build·Report/collection·ZIP·publish는 후속 |
| 독립 공유 패키지·게시 — 후속 | 저장/결박·전달 명령 없음 | `analytics build`, `analytics publish` |
| 오류·내부 상태 | `AnalyticsError`, `Project.state_dir`, `Project.workspace_dir`(kit 0.6.0부터. 그 전은 `LabError`, `Project.lab`, `Project.report`). kit 0.8.0부터 값은 표식이 고른 배치(`Project.layout`)의 것: `analytics.json` 프로젝트는 `.analytics`·`workspace/`, `lab.json` 프로젝트(읽기만)는 `.lab`·`report/`. 함께 더한 이름: 가져온 데이터 디렉터리 `Project.imported`(그 전 `Project.raw`, 값 `data/imported/`·옛 배치 `data/raw/`), 읽기만 하는 옛 상태 디렉터리 `Project.legacy_state_dir`(`.lab`), 쓰기를 받아들인 표식의 배치로 경로를 다시 정하는 `Project.lay_out`(`core.admit`이 부름), init이 만든 프로젝트의 응답용 요청 id `Project.request_id` | 유지 |
| 표시 구현 | 모듈 `workspace.py`, 자원 `workspace.js`·`workspace.css`, 확장의 `schedule_workspace_render`(kit 0.6.0부터. 그 전은 `report.py`, `report.js/css`, `schedule_report`) | 유지. 렌더러는 함수로 두고 `WorkspaceRenderer` 클래스를 만들지 않는다([#13 설계](design/RECORD-WRITER-AND-MIGRATION.md#9-미뤄-받은-이름과-pr) 9절) |
| Workspace 빌드 결과(kit CLI의 결과 JSON) | 키 `workspace`, run의 빌드 실패 `workspace_error`(kit 0.6.0부터. 그 전은 `report`·`report_error`). 값의 경로는 kit 0.8.0부터 `workspace/…`(그 전과 0.8.0의 `lab.json` 프로젝트는 `report/…`) | 유지 |
| 기록 writer(#13의 ①, kit 0.7.0) | 모듈 `writer.py`(계층 `writer`). 표식 판정 `marker_state`와 그 결과 `MarkerState`(상태 `none`·`legacy`·`current`·`unknown`·`invalid`·`conflict`, ②부터 `incomplete`), 쓰기 게이트 `admit`(읽기의 부수 쓰기는 `side=True`), 읽기의 판정 `readable`, 이 kit이 쓰는 표식 형식 `WRITABLE`(kit 0.8.0부터 `analytics.project/2`, 그 전 `lab.project/1`), 작성 출처 `Origin`, 옛 배치의 저장 이름표 `LegacyLayout`(kit 0.8.0 전에는 이 kit이 쓰던 배치). 읽기 모델의 Visualization 보기 `run_visualizations`·재실행 비교 보기 `run_reproduction`(`relations`), journal 목록 `JournalLists`(`core`) | 유지. 새 형식의 이름표와 쓰기 클래스는 아래 '공통 writer' 행 |
| 공통 writer(#13의 셋째 PR ③) | 같은 모듈의 새 형식 이름표 `Layout`(값 `LAYOUT`; 표식 판정이 배치를 고르는 `layout_of`. `LegacyLayout`에 잠금 이름 `journal_lock`·`run_lock`·`workspace_lock`), 쓰기 클래스 `Writer`(`record`·`update`·`write_run`·`write_dataset`·`locked`, `record`가 잠금 밖에서 거절하는 것을 쓰지 않고 판정하는 `check`; 잠금 안의 문맥 `Locked`, 쓰기의 결과 `Written`), 기록 가족 표 `FAMILIES`(키 `project`·`event`·`run`·`dataset`·`trust`·`migration`), 사람·에이전트가 기록하는 kind `RECORD_KINDS`와 kit 자신의 kind `KIT_KINDS`, 덧붙이기 `append_line`, 프로젝트 id `new_project_id`, 줄 없이 하는 본문 검사 `check_text`, 나중에 쓸 기록(SDK의 run 안 기록)의 줄을 재는 가장 넓은 seq `SEQ_BOUND`, kit이 만드는 본문이 다른 곳에 통째로 남는 필드를 인용하는 `excerpt`와 그 길이 `EXCERPT_CHARS`(4,000자), `lab.json` 프로젝트 쓰기의 거절 `migration_required`와 그 안내 `migration_hint`·`MIGRATE_COMMAND`, 다른 프로젝트나 커밋하지 않은 전환 목적지 안의 새 프로젝트를 찾는 `project_above`(init과 전환의 목적지 규칙, 전환 기록은 2026-10-09 소유자 결정)와 그 거절 글의 `inside`. `core`의 얇은 진입 `core.record`·`core.writer_for`, 맥락 동기화 전의 기록 판정 `core.check_record`와 동기화가 버전을 만들지 `core.context_pending`, 응답의 요청 id `core.request_id_of`, kit 자신의 기록이 주는 참조 `core.kit_reference`(`about`·`observed`·`author`), 상태 글의 `core.OLDER_FORMAT_LINE`·`core.older_project_above`. 읽기 모델(`relations`)의 상태 재생 `Replay.state_of`·`Journal.event_of`와 결박용 조회 `latest_version`·`keys_named`·`runs_of_request`·`import_record`·`latest_output` | 유지. kit 0.8.0부터 모든 쓰기 진입점이 이 writer로 쓰고 `core.NOTE_KINDS`·`core.add_note`·`core.append_event`·`MAX_EVENT_TEXT`는 없음([설계](design/RECORD-WRITER-AND-MIGRATION.md) 4.4·4.5·9절) |
| 전환 엔진(#13의 ②, kit 0.7.0) | 모듈 `migration.py`(계층 `migration`, 수준 3)의 `migrate`. 포트 메서드 `FileLocks.try_exclusive`(기다리지 않는 잠금, `LockBusy`·`LockUnavailable`), `AtomicFiles.publish_new`(없을 때만 이름을 줌, `LinkUnsupported`)·`flush_handle`·`flush_dir`, kit 0.8.0의 `AtomicFiles.rename_new`(이미 있는 이름을 바꾸지 않는 rename: Linux `renameat2(RENAME_NOREPLACE)`·macOS `renamex_np(RENAME_EXCL)`·Windows `os.rename`, 없으면 `RenameUnsupported`; 링크가 없는 목적지의 표식 게시). 매핑을 읽는 `core.MigrationMapping`·`mapped_identity`·`migration_mapping`. 전환의 작성 주체(ledger의 `start`·보고서·응답에 같은 값)를 만드는 `migration.said_by`. macOS의 AppleDouble 판정 — 이름만 보는 `core.appledouble_name`(`._<이름>`)과 그 이름의 일반 파일(lstat)인지 보는 `core.is_appledouble`(2026-10-09 소유자 결정과 같은 날의 보정으로 운영체제 메타 파일, 폴더·링크는 아님) — 과 init·전환이 함께 쓰는 빈 디렉터리 판정 `core.empty_directory_entry`(`.DS_Store`·AppleDouble 파일) | 유지. 명령 `analytics migrate`는 kit 0.8.0(#13의 셋째 PR)부터 |
| Workspace 내부 식별자 | `window.__ANALYTICS_BUILD__`, 화면 언어의 전역 `AnalyticsI18n`과 저장 키 `analytics-language`(kit 0.16.0, #96), 브라우저 저장 키 `analytics-live`·`analytics-scroll:<경로>`, plotly 템플릿 `analytics`(`plotly_white+analytics`), capture의 표식 `__analytics_wrapped__`(kit 0.6.0부터. 그 전은 `__LAB_BUILD__`, `lab-live`·`lab-scroll:`, `lab`, `__lab_wrapped__`). 새 저장 키가 없으면 옛 키를 한 번 읽고 옛 키는 남긴다. `build.js`는 연 채로 둔 옛 페이지가 다시 읽도록 옛 전역도 같은 값으로 정한다. 저장하는 그림 JSON에는 템플릿 이름이 없다 | 유지 |

위 CLI 작업은 Aelix의 `/analytics` 아래에서도 같은 의미를 사용한다. 지원하는 하위 명령은 실제 구현 기준으로 공개한다.
`ReportRenderer`는 이전 아키텍처 제안의 명칭이었으며 현재 코드의 클래스가 아니다. 새 설계에서는 Workspace 표시와 Report 형식 처리를 구별한다.
`record`는 종류가 있는 journal 기록 쓰기다. 임의 저장소 쓰기·SQL·실행·게시를 하나의 거대한 action 도구로 합치는 뜻이 아니다.
SDK의 `read`, `scan`, `sql`, `save`, `datasets`, `project`, `path`, `run_id`는 역할이 맞으므로 유지한다.
SDK `scan`은 지연 데이터 읽기이고 정적 코드 스캔은 `static_scan`으로 표현한다. 기존 SDK와 모델 도구의 인자는 같다고 추정하지 않고 각각 계약을 문서화한다.

명칭 전환 후에도 기본 모델 도구는 같은 여섯 역할이다. 구·신 도구를 동시에 등록해 열두 개로 늘리지 않는다.
`analytics_report`, `analytics_memory` 등을 용어 하나마다 신설하지 않는다. 더 긴 이름의 실제 schema·호출 토큰 비용도 측정한다.

## 3. 파일·저장 형식·환경의 명칭

| 대상 | 전환 목표 | 의미와 보존 기준 |
|---|---|---|
| 프로젝트 표식 `lab.json` | `analytics.json` | 프로젝트 정체성·형식·설정 |
| 관리 디렉터리 `.lab/` | `.analytics/` | context 버전·작업 상태·보조 로그·캐시. 폴더 전체가 버려도 되는 캐시는 아님 |
| 화면 `report/` | `workspace/` | 재생성 가능한 Workspace HTML·자원 |
| 개별 보고서 | `artifacts/reports/<report_id>/<revision_id>/` | 보존할 본문·첨부·manifest. Workspace와 다른 수명 |
| 독립 공유 패키지(구현 전) | `.analytics/publications/<publication_id>/publication.json`, `analytics.publication/1` | [ADR-0014](decisions/0014-publication-record-contract.md)의 채택 계약. `publication_id`는 `pub-`와 소문자 ULID. 결박 이벤트와 패키지 해시가 정체성을 남기며 build 폴더는 보존 정본과 구별 |
| 실행의 시각화 `runs/<id>/charts/` | `runs/<id>/visualizations/` | 새 실행의 저장 경로. 과거 manifest·경로·바이트는 legacy 대응으로 보존 |
| 가져온 데이터 `data/raw/` | `data/imported/` | 가져오며 형식·타입을 변환했을 수 있는 데이터. 외부 원본 파일과 구별 |
| 파생 데이터·실행 코드 | `data/derived/`, `scripts/`, `runs/` | 이름 유지. 파생 데이터라는 이유만으로 언제든 재생성 가능하다고 가정하지 않음 |
| 맥락·사건 기록 | `CONTEXT.md`, `journal.jsonl` | 이름 유지. 현재 맥락과 시간순 사건의 구별 |
| 신뢰 목록 | `analytics-projects.json`(kit 0.8.0부터 이 kit이 씀) | 호스트의 기존 agent 설정 위치 아래. 활성화 신뢰 범위만 의미. 옛 `lab-projects.json`(`lab.trust/1`)은 읽기만 하고 그 경로는 `lab.json` 프로젝트만 활성화(설계 6.14) |
| schema namespace `lab.*` | `analytics.project`, `analytics.event`, `analytics.run`, `analytics.dataset`, `analytics.trust`, `analytics.migration` | 가족 이름과 형식 버전을 함께 식별. 기존 `/1` 원문을 새 namespace로 덮어쓰지 않음. 정한 버전: `analytics.project/2`·`analytics.event/2`(ADR-0006), `analytics.run/2`(Visualization `visualizations[]`·`visualization_id`·`sha256`·`data_sha256`, 재실행 비교 `reproduction.visualizations`, `via`의 `analytics.` 접두)·`analytics.dataset/1`(`data/imported/`)·`analytics.trust/1`(`{"schema", "projects"}`) — [#13 설계](design/RECORD-WRITER-AND-MIGRATION.md) 2.6·2.7절. kit 0.7.0부터 읽고, kit 0.8.0부터 이 kit이 쓰는 형식(옛 `lab.*/1`은 읽기만). `analytics.migration/1`(전환의 계획·매핑·보고서, `record`가 `plan`·`mapping`·`report`)은 #13의 둘째 PR(kit 0.7.0)의 전환 엔진이 쓰고 그 kit이 읽음 |
| run 입출력의 `via` 값 | `analytics.read`·`analytics.scan`·`analytics.sql`·`analytics.save`·`analytics.visualize`(SDK 함수의 철자, kit 0.8.0부터. 그 전 `lab.read`·`lab.scan`·`lab.sql`·`lab.save`·`lab.chart`) | `plt.show`·`fig.show`·`auto`·`file`·`diff`·`declared`·`scan`과 라이브러리 읽기(`polars.read_parquet` 등)는 그대로. 옛 run 기록의 값은 고쳐 쓰지 않음(설계 2.6, OQ5) |
| 제어 환경 변수 | `AELIX_ANALYTICS_*` | `KIT`은 `RUNTIME_PATH`, `PROJECT`, `UV`, `TRUST_FILE` 등은 의미 유지. kit 0.3.0부터 현재 이름이고, 옛 `AELIX_LAB_*`는 오류 |
| 실행 환경 변수 `LAB_*` | `AELIX_ANALYTICS_*` | `ACTOR`, `SESSION`, `RUN_ID`, `RUN_DIR`, `PROJECT`, `STYLE`의 역할 유지. `LAB_PROJECT`와 `AELIX_LAB_PROJECT`는 `AELIX_ANALYTICS_PROJECT` 하나가 됨. kit 0.3.0부터 현재 이름이고, 사용자가 설정하던 옛 이름은 오류 |
| 내부 배포 자원 | `core`, `runtime`, `workspace`, `reports`, `publishing`, `templates` | 책임에 따른 이름. `labcore`·`labkit`·`pack`을 새 공개 브랜드로 남기지 않음 |
| 실행 루트 | package `aelix_analytics` 하나만 담은 디렉터리: 체크아웃의 `src/`, 설치본의 `aelix_analytics/kit/`(같은 소스의 사본) | 진입점이 run의 프로젝트 환경에 `PYTHONPATH`로 주는 유일한 경로(2026-10-06부터). 설치된 package 자신은 실행 루트가 아니어서 `python -m`을 거절한다(오류 코드 `not_runtime_root`). `RUNTIME_PATH`(위 행)가 가리킬 디렉터리 |

호스트가 정한 `AELIX_CODING_AGENT_DIR` 등과 외부 라이브러리의 `MPLBACKEND`, `PYTHONPATH`는 확장 소유 이름이 아니므로 바꾸지 않는다.
`ANALYTICS_API`(kit 0.6.0 전 `LAB_API`)처럼 시험 스크립트에서만 쓰는 표식은 공개 설정으로 승격하지 않는다. 확장이 kit에 취소를 요청하는 파일의 경로
`AELIX_ANALYTICS_CANCEL_FILE`(옛 `LAB_CANCEL_FILE`)과 확장이 kit 자식에 실행 수단(`means`)을 알리는 `AELIX_ANALYTICS_MEANS`(kit 0.8.0)도 내부 프로토콜이라
위 역할 목록에 넣지 않는다([현재 계약](CONTRACTS.md) D). kit 0.8.0부터 aelix 확장이 aelix 프로세스의 환경에 `AELIX_ANALYTICS_ACTOR=agent`와
`AELIX_ANALYTICS_SESSION`을 넣고 사람의 `!` 명령 가운데 줄 전체가 kit 명령 하나인 것에는 `human`을 넣지만, 이름은 위 행의 `ACTOR`·`SESSION` 그대로다(새 이름이
아니다, [현재 계약](CONTRACTS.md) A).
스키마 숫자는 의미 변경에 따라 정한다. 단순 namespace 전환과 B1의 새 의미 도입을 같은 변경이라고 가정하지 않는다.
공유 파일의 `publication.json`과 원래 보고서의 manifest도 각각의 버전을 가진다.

## 4. 분석의 도메인 용어

| 코드 / 기본 영어 표시 | 뜻 | 구별할 것 |
|---|---|---|
| `project` / Project | 여러 세션에 걸쳐 이어지는 분석의 목적·기록·파일 단위 | Workspace 화면, 호스트 session |
| `analysis_focus` / Analysis focus | 현재 목적·지표·질문·다음 확인의 연결 | 고정된 분석 절차나 하나의 실행 |
| `goal` / Goal | 왜 분석하고 누구의 어떤 판단을 돕는가 | 지표의 수치 목표 Target |
| `question` / Question | 이번에 답하려는 질문 | 작업 지시나 확정된 발견 |
| `metric` / Metric | 계속 관찰할 측정 개념 | 컬럼, 차트, KPI와 무조건 동일하지 않음 |
| 지표 정의 참조 / Metric definition | 계산 의미·단위·모집단·분모·규칙의 특정 버전 | 실행 코드 버전 |
| `metric_observation` / Metric observation | 정의를 특정 데이터·기간·범위에 적용한 결과 | 정의 자체, 센서 원시 샘플 |
| `dimension` / Dimension | 지표를 나누어 관찰하는 축 | 모든 데이터 컬럼 |
| `scope`, `unit`, `grain` / Scope, Unit, Grain | 대상 범위·측정 단위·한 관측의 단위 | 현재 질문이나 입력 파일명만으로 추정하지 않음 |
| Target / Baseline / Threshold | 원하는 값 / 비교 기준 / 판정 경계 | 분석 목적, 과거 baseline 제품 이름, 토큰 성능 목표 |
| `finding` / Finding | 무엇을 관찰·해석했는지와 근거·한계를 남긴 기록 | Report 본문 전체, 자동 인증된 사실 |
| `decision` / Decision | 무엇을 하거나 어떤 기준을 채택했는지 | 발견 자체나 제품의 실행 승인 |
| `assumption` / Assumption | 아직 확인되지 않았지만 작업에 사용하는 전제 | 검증된 사실 |
| `todo` / Next step | 해야 할 작업 | 질문의 답이나 실행 run |
| `note` / Note | 별도 종류가 없는 자유 메모 | 모든 journal 사건을 뜻하는 상위 이름 |
| `workflow_feedback` / Workflow feedback | 분석 도구를 쓰며 막히거나 불편했던 점 | 데이터의 결측 gap, 분석 결론, GitHub Issue |
| Insights | Findings·Asset candidates·기록 상태를 읽는 화면 | `insight`라는 중복 원본 종류를 자동 신설하지 않음 |
| Analysis asset / 분석 자산 | 검사·등록 경로로 발전시키는 계산 구현 또는 사용 절차 | 등록·실행·유용성은 별개. #17 구현 전 계약은 [ADR-0010](decisions/0010-analysis-assets-and-suggestion-records.md) |
| `asset_candidate` / Asset candidate | 함수·모듈·패키지·skill로 발전시킬 자산 후보 | 분석 발견, 실제 등록본. 기존 `reuse_proposal`은 과거 종류 그대로 읽음 |
| Asset suggestion | 정책을 통과했거나 명시적으로 요청한 후보 안내 | 후보 작성·검사·수락·등록·실행과 구별 |
| Skill / Analysis code | 사용·해석·검사 절차 / 실제 계산 구현 | 둘을 한 가지 형식으로 통합하지 않음 |
| `checkpoint` / Analysis checkpoint | 근거를 연결한 작업 구간 요약 | 전체 데이터 스냅샷·트랜잭션 복구 지점 |
| Context / Project memory | 지금 필요한 작업 맥락 / 검색·재개 가능한 프로젝트 지식 | 사용자 개인 메모리, 전체 대화 덤프 |
| `event` / Journal entry / Timeline | 기록된 사건 / journal 항목 / 시간 흐름을 읽는 화면 | 세 이름을 별도 진실 저장소로 만들지 않음 |

기존 `gap`은 과거 Workflow feedback으로 읽는다. kit 0.8.0부터 새 기록은 `workflow_feedback`으로 쓰고, 입력 `gap`은 새 이름을 알리는 `bad_kind`다(별칭 없음). Findings나 Skill proposals로 자동 변환하지 않는다.
기존 그래프 제안·Engine의 `Claim`은 해당 계약의 표현이다. 현재 제품에서는 발견을 `finding`으로 표현하고, 외부 타입과의 대응은 adapter에서 명시한다.
명확한 지표가 있으면 즉시 연결하며, 모호한 지표·미연결 발견·자유 보고서도 허용한다. 용어집은 분석을 시작하기 위한 필수 입력 양식이 아니다.

## 5. 데이터·근거·실행·산출물

| 용어 | 뜻과 사용할 위치 | 오해를 피할 기준 |
|---|---|---|
| Dataset / Dataset observation | 논리적 데이터 대상 / 특정 시점에 확인한 파일·해시·범위 | 관측 해시만으로 과거 바이트가 보존되었다고 하지 않음 |
| Data snapshot | 실제 보존한 특정 버전의 데이터 바이트 | 현재 경로를 가리키는 관측·참조와 구별 |
| Source file / Imported data / Derived data | 외부 입력 / 가져와 변환한 데이터 / 분석으로 만든 데이터 | `raw`를 무변환 원본 또는 원시 센서값의 보장으로 사용하지 않음 |
| SQL view | SQL에서 조회할 수 있는 등록 이름 | Workspace 화면이나 차트 View와 구별. 코드의 `views`는 `sql_views`로 범위를 명시 |
| Dataset profile / Preview | 스키마·통계 요약 / 일부 행의 표시 | 분석 방법의 sensor profile, AgentProfile, 전체 데이터와 구별 |
| Column / Field | 표의 열 / 구조화 객체의 속성 | 데이터의 원래 열 이름은 번역하거나 변경하지 않음 |
| Visualization / Visualization version | 분석 표현 객체 / 특정 run에서 보존한 표현 | 화면 전체·원본 데이터셋·최신본으로 바뀌는 인용과 구별 |
| Chart | 선·막대·분포 등 통계 차트 | Visualization의 한 유형이며 상위 객체 전체의 동의어가 아님 |
| Attached data / Plotted data | 시각화에 첨부한 표 / 실제 플롯 요소에 대응하는 데이터 | 대응 범위가 확인되지 않으면 Attached data로 표시 |
| Evidence / Evidence reference | 판단을 확인하는 자료 / 그 자료의 정확한 버전 참조 | 참조만 있다고 자료 보존·결론 검토가 완료된 것은 아님 |
| Provenance / Relations / Row lineage | 생성·관측 출처 / 연결 관계 / 행 수준 대응 | 그래프의 연결을 완전한 행 계보나 인과관계로 부르지 않음 |
| Analysis map | 스크립트 단위의 프로젝트 흐름: 상자는 스크립트와 그 run, 화살표는 한 스크립트의 run이 쓰고 다른 스크립트의 run이 읽은 같은 경로·바이트의 파일(Workspace 탭, kit 0.18.0, [계약 C14](CONTRACTS.md#c14-overview의-분석-상태와-분석-지도-kit-0180-98)) | 화살표는 생산자 후보이며 확정 연결·행 계보가 아님. Provenance(한 대상에서 거슬러 올라가는 출처)와 구별 |
| Provenance trail / Where it comes from / What uses it | 한 대상(기록·run·시각화 버전·데이터 파일)에서 기록된 인용과 run의 입력·출력·생산자 후보를 run 단위로 따라간 경로 / 그 대상이 무엇에서 만들어졌는지 / 무엇이 그것을 썼는지(관계 페이지의 `Provenance` 탭과 `analytics_status`의 `part=relations` `direction=sources`·`uses`, kit 0.24.0, [계약 C18](CONTRACTS.md#c18-출처-추적-kit-0240-100)). 상자의 상태는 `Unchanged`·`Changed since read`·`Changed since written`·`Missing now`·`Unknown`, 잇는 말은 `Read by`·`Read`·`Wrote`·`Written by`·`Made`·`Made by`·`Cited by`·`Cites` | 결론의 증명·행 계보·인과가 아님. 생산자 후보는 같은 바이트라는 관측이며 확정 연결이 아님. 분석 지도(스크립트 단위의 프로젝트 흐름)와 구별 |
| Version history / Version n of N | 한 대상(시각화 key·데이터 파일·스크립트 코드·CONTEXT.md)의 버전들을 시간 순서로 보인 것 / 그 가운데 몇 번째인지(Workspace, kit 0.19.0, [계약 C15](CONTRACTS.md#c15-버전-기록-kit-0190-99)) | 번호는 화면을 만들 때 세는 표시 값이며 주소·인용은 run ID·sha256·스냅샷 이름. Correction history(정정 이력)는 번호 없는 기록의 정정 순서. 사용처 수는 결론의 근거 강도가 아님 |
| Run / Rerun | 기록된 분석 코드 실행 / 이전 실행과 연결된 재실행 | 호스트 Session·일반 도구 호출·분석 과제 전체와 구별 |
| Task / Session / Tool call | 사용자가 해결하려는 과제 / 호스트 대화 단위 / 개별 도구 호출 | 세 수치를 모델 요청 횟수·토큰과 혼용하지 않음 |
| Input / Output change | 실행에서 관측·선언한 입력 / 생성·수정·삭제한 파일 변화 | `outputs`에 잡혔다고 모두 보존한 artifact는 아님 |
| Artifact / Report / Report collection | 보존한 산출물 / 설명 목적의 보고서 / 구성원 버전을 고정한 보고서 묶음 | 모든 그림·CSV·작업 파일을 Report로 자동 분류하지 않음 |
| Render / Build / Publish | Workspace·저장 내용 표시 / 독립 공유 패키지 생성 / 정해진 대상에 전달 | 본문 작성·분석 실행과 별개 |
| Publication / Publish receipt | 고정된 공유 패키지 / 전달 대상·결과·주소 기록 | 보고서의 내용 버전 또는 확장 패키지 릴리스와 구별 |
| Export / Release | 정해진 포맷·범위로 내보내기 / 확장 제품 버전 배포 | Publish의 동의어로 무분별하게 쓰지 않음 |

공통 읽기 모델의 `build`와 `report`처럼 겹친 필드는 의미에 맞게 `render_id`, `workspace`, `publication_id`, `report_id`로 구체화한다.
`ReportRenderer`는 보고서 본문 형식별 표현에만 쓰고, 프로젝트 전체 인터페이스는 `WorkspaceRenderer`로 부른다.
이름 변경만으로 과거 데이터의 행 대응·스냅샷·재현 보장이 추가되는 것은 아니다.

### Visualization의 범위

공통 객체는 `Visualization`, 컬렉션은 `visualizations`, 식별자는 `visualization_id`, 특정 실행의 참조는 `run_id + visualization_id`를 사용한다.
기본 탭은 `Visualizations`로 하고, 기존 `charts` 필드·`chart_id`·`chart_version`은 legacy reader에서 대응한다.
새 참조 종류의 예는 `visualization_version`이다. 논리적 이름과 당시 저장 버전의 구별은 그대로 유지한다.

통계 차트, 지도, 다이어그램, 표현을 구성해 저장한 표, 복합 시각화를 같은 상위 계약으로 다룰 수 있게 한다.
그림 유형과 `matplotlib`·`plotly` 같은 생성 라이브러리, PNG·HTML 같은 보존 형식을 다른 속성으로 관리한다.
이미지나 임의 HTML에서 유형을 알 수 없으면 미확인으로 남기고 닫힌 유형 목록에 맞추기 위해 자유 표현을 거절하지 않는다.

Dataset은 분석 대상이고 Visualization은 그 대상을 표현한 결과다. 단순 데이터 미리보기나 모든 SQL 결과를 자동으로 새 Visualization으로 기록하지 않는다.
근거 관계 그래프는 기본적으로 프로젝트 기록의 파생 읽기 화면이다. 명시적으로 산출물로 저장한 경우에만 별도 Visualization·artifact로 다룬다.
Report는 Visualization·설명·발견을 선택해 구성한 문서이며, Visualization 하나와 같은 객체로 합치지 않는다.

첫 구현은 현재 그림·HTML·파일 캡처의 기능을 보존하며 이름과 공통 정체성을 정리한다. 지도 편집기·새 렌더러를 모두 베타에 제공한다는 뜻은 아니다.
기존 차트의 `script::id` 관계는 논리적 묶음의 legacy 키로 읽는다. 이름 변경만으로 run에 고정되지 않은 옛 인용을 정확한 버전 참조로 바꾸지 않는다.
추후 선택 기반 분석도 원래 Visualization 버전·첨부 범위·행 대응을 확인한 후 연결한다. 새 모델 도구를 유형마다 추가하지 않는다.

## 6. 상태·시간·버전·측정의 정확한 표현

| 현재 표현 또는 혼동 | 목표 표현·의미 |
|---|---|
| 요청 응답 `ok` / run `status=ok` | 요청 처리 성공과 Run succeeded를 구별. 원본 저장값의 전환은 schema별 대응으로 처리 |
| 실행·기록·표시의 한 가지 성공 표시 | Execution status / Recording status / Render status를 구별 |
| `reproduction`, `verdict`, ‘재현 검증 완료’ | Rerun comparison, `rerun_comparison`, `comparison_result`. Matched / Different / Inconclusive / Run failed를 비교 범위와 함께 표시 |
| 하나의 Verified·Quality 배지 | Analysis checks / Conclusion review / Record coverage / Tool usage를 별도로 표시. Overview의 Analysis status는 근거 연결·실행·입력 변경·열린 항목·재실행 비교·분석 검사와 결론 검토를 칸마다 따로 세며 하나의 점수로 합치지 않음(kit 0.18.0). 기록이 없는 칸은 `None recorded`(열린 항목이 없으면 `Nothing open`, 입력이 바뀐 시각화가 없으면 `No input changes`), 하지 않은 재실행 비교는 `Not run` |
| `Freshness`, `stale` | Input changes와 확인 시점. `InputComparison`은 입력·코드 비교를 구별하고, 입력이 바뀌었다는 이유로 과거 결론을 거짓이라고 하지 않음 |
| `retired` chart | Absent from latest run. 최신 실행에 없다는 뜻이며 삭제·철회·사용 중단 선언은 아님 |
| `has_data` | Attached data present. 전체 플롯 데이터 보존 여부는 별도 범위 |
| `kind`·`via`의 여러 의미 | event kind / dataset origin / visualization type·backend / file format / capture method를 스코프별로 명시. `kind`만 전역 검색·공통 enum으로 합치지 않음 |
| `schema` | Record schema와 Table schema를 구별. 출력 표 구조를 `record_schema`로 오해하지 않음 |
| `id`, `name`, `title`, `key`, `path` | 안정된 정체성 / 범위 안의 조회 이름 / 사람이 읽는 제목 / 특정 조회 키 / 파일 위치. 서로 자동 대체하지 않음 |
| `refs.other` | Unresolved references. 이미 확정한 근거 관계로 승격하지 않음 |
| `ts` | 이벤트는 `recorded_at`, 조회 실행 등은 의미에 맞는 시각. 모든 로그의 `ts`를 기계적으로 같은 필드로 바꾸지 않음 |
| `created_at`, `based_on`, `scope` | 보고서 저장 시각 / 작성 근거 기준 / 설명하는 대상 범위. 게시 시각과 구별 |
| `source_revision` | Source revision은 조회가 읽은 기록 기준. Git commit·전체 데이터 스냅샷의 동의어가 아님 |
| `revision` 이벤트와 내용 버전 | 상태 변경 이벤트는 `record_update`로 명확히 하고, `revision_id`는 저장한 내용 버전에 사용 |
| `resolves`, `supersedes`, withdrawn | 작업·질문의 닫힘 / 이전 내용을 대체 / 철회. 발견의 닫힘을 반박·철회로 소급 해석하지 않음 |
| Latest / Current / Valid | 가장 최근 저장본 / 현재 초점·선택 / 적용 범위에서 유효함. Latest가 곧 승인된 결론이라는 뜻은 아님 |
| Unknown / Not recorded / Missing / Not included / Unsupported | 미확인 / 기록 안 됨 / 있어야 할 자료 없음 / 공유 범위에서 제외 / 기능 미지원. 모두 ‘없음’으로 합치지 않음 |
| Observed / Declared / Inferred / Unverified | 관계 근거의 관측·선언·추론·미검증 범위. 분석 결론의 검토 상태와 별도 축 |
| Proposed / Trial / Validated / Registered | 재사용 후보의 제안·시험·명시 범위 검사·등록 상태. Registered를 현재 분석의 승인이나 skill 자동 실행으로 해석하지 않음 |
| `on_demand`, `always_on` | On demand / Automatic suggestions. 자동 모드도 근거·임계값·중복·빈도를 통과할 때만 제안. 저장된 모드 의미 유지 |
| `<!-- 항상 -->`, `<!-- always -->` | 새 템플릿은 `<!-- pin -->`으로 맥락 선택 우선순위를 표시(#10부터). 읽기는 세 표식을 모두 고정으로 보고, 섹션 이름에서 지운다. skill의 always-on 모드와 구별 |
| Token budget | Token usage target / Performance target. 측정 목표이며 강제 절단·거절 한도가 아님 |
| Threshold / Cooldown / Timeout / Page size | 실제 제안 조건·알림 간격·실행 제한·조회 크기. 측정 목표와 다른 설정임을 명시 |
| Input tokens / Cached input tokens / Output tokens | provider별 포함 관계를 정규화해 계산. 캐시 입력을 이중 합산하거나 도구 호출 수를 토큰으로 쓰지 않음 |
| 데이터셋·입력의 생산자(`producer`) | 생산자 판정 `producer_state`(입력은 `producer`): `resolved`(같은 경로·바이트를 출력한 성공 run 하나) / `ambiguous`(여럿, `producer_candidates`에 모두) / `none` / `unknown`(해시 없음). 후보는 같은 바이트를 출력했다는 관측(`observed`)이며 확정 연결이 아니다. 소비 run보다 늦게 시작한 run은 후보가 아니고, 마지막 run을 고르지 않는다. 화면은 `same bytes as run X's output`(여럿이면 `same bytes as N runs: …`, Workspace는 `Several possible sources`, kit 0.17.0 전에는 `Producer ambiguous`), stale인 후보는 `upstream candidate`로 표시 |
| 근거 버전의 고정 방식 | `version_binding`: `author`(기록을 쓴 쪽이 버전까지 지정) / `inferred`(이름만 받아 kit이 쓰는 시점의 최신 성공 버전으로 결박. 확정 연결로 세지 않음) / `none`(논리 키만). `<!-- pin -->`은 맥락 선택 우선순위로만 씀 |
| 참조를 찾았는지만 보는 판정 | 해석 상태 `resolved` / `content_changed` / `missing`(`record_absent`·`digest_mismatch`·`file_absent`) / `version_unbound` / `ambiguous` / `unknown`(`no_digest`·`unresolved_name`) / `unsupported`, 게시본의 `not_included`. 읽기의 결과이며 기록에 쓰지 않음. `digest`는 대상 기록이 남긴 해시를 옮긴 값. Workspace는 판정을 표시와 글자의 배지로 보인다(kit 0.17.0, [계약 C13](CONTRACTS.md#c13-목록관계-표현-종류별-목록-좁은-화면-kit-0170-97)): `Exact version`·`Version not named`·`Several matches`·`Unknown name`·`No hash recorded`·`Not found when cited`·`File missing now`·`Cited bytes differ`·`Changed since cited`·`Not supported`. 저장 값은 관계 페이지의 Details에 원문 그대로 |
| 근거가 연결됨 | 확정 연결은 `version_binding=author`이고 `basis`가 observed·declared이며 `resolved`인 참조뿐. 결론의 검토와 별개 |
| 참조의 관계 | `relation`: `supports`·`contradicts`(작성자의 주장) / `cites`(중립 인용) / `uses` / `derived_from` / `generated_by` / `about` / `includes` / `resolves` / `supersedes` / `updates`. 관계 근거 `basis`와 다른 축 |
| 기록 기준의 `through_seq`, 마지막 seq | 영속 기준은 `as_of_seq`(그 시점까지 포함한 journal seq)와 `context_version`(CONTEXT 버전 파일 이름). checkpoint 초안의 `through_seq`를 대체 |
| 게시본의 `content_seq`, `content_recorded_at`(투영·미리보기 구현) | [ADR-0014](decisions/0014-publication-record-contract.md)의 채택한 내용 기준. `as_of_seq` 이하에서 정상 게시 계열만 제외한 마지막 seq와 기록 시각. 후보가 없으면 0·null. 전체 journal 기준·build 순서·Report 작성 기준과 구별 |
| 게시본의 `package_sha256`, `content_sha256`(구현 전) | `publication.json` 최종 바이트의 해시 / 정체성 파일 셋을 뺀 파일 목록의 내용 해시. 패키지 정체성과 반복 build의 내용 비교를 구별. 결론 검토·분석 검사 인증은 아님 |
| 조회 페이지의 이어 읽기 | cursor는 `source_revision`에 묶인 불투명 토큰. 기준이 바뀌면 `stale_cursor`, 읽는 동안 계속 바뀌면 `updating` |
| 공유 미리보기 판정 | `profile_unknown`, `scope_unknown`, `scope_unsupported`, `analysis_state_invalid`, `exclusion_invalid`, `scope_invalid`; 읽기 `records_updating`·`records_unsupported`, CONTEXT `context_missing`·`context_changed`·`context_unverified`·`context_unsupported`, 사본 `source_changed`, 검사 `leak_found`·`inspection_failed`·`package_check_failed`, 표시 자원 `renderer_unavailable`. 미리보기의 `checks.inspection`은 inspected/pinned_scanned/not_inspectable 수이며 분석 검사·결론 검토가 아님 |
| 공유할 수 없는 Report 버전(kit 0.27.0) | `report_not_shareable`과 `details.reason`: `not_saved`(저장 시도), `withdrawn`, `missing`·`content_changed`, `format`, `scripts`·`remote_resources`, `link`, `path`, `member_missing`, Report 판정의 problem 이름. Report는 버전 단위로만 공유하며 사람이 넘기는 옵션은 없음. 출력·manifest의 `scope.reports`는 고른 정확한 버전(`report_id@revision_id`). 보기 페이지의 글 `Report version`, `Version n of N`, `Withdrawn`, "A later version existed when this copy was built — not included"(build 때의 관측) |
| 같은 요청의 재시도 | `request_id`(쓰기의 선택 필드, 대소문자를 구별하지 않고 비교)와 `request_fingerprint`(요청 의도의 RFC 8785 JSON의 sha256). 같은 키에 다른 지문이면 `request_conflict`. 베타의 보장은 Report 저장과 게시(`publication_id`+대상)뿐 |
| 버전·상태의 충돌 | `revision_conflict`(기대한 `previous_revision`이 head가 아님), `state_conflict`(`record_update`의 기대 상태 불일치나 허용하지 않는 전이), 정정이 갈린 기록의 `conflict`. 최신 시각으로 한쪽을 고르지 않음. 공통 writer의 `state_conflict` `details`: `target`·`expected_state`·`new_state`, `reason`(`target_missing`·`target_ambiguous`(`candidates`)·`no_state`(상태가 없는 기록)·`state_differs`·`transition_not_allowed`), 지금 상태 `state`, 그 상태에서 허용하는 전이 `allowed`, 그 상태를 만든 이벤트 `made_by`(`id`·`request_id`) |
| 쓰기의 응답과 본문 상한(공통 writer) | 쓰기 결과의 `request_id`(호출자가 준 값, 없으면 kit이 만든 값이며 기록에는 준 값만), 이름으로 받은 참조의 결박 `bindings`(이름마다 `input`·`ref`·`binding`, 결박하지 못하고 후보가 있으면 `candidates`), 다른 기록의 상태를 바꾸는 참조(`resolves`·`supersedes`·`updates`)의 대상 `targets`(`event_id`·`relation`·`state_before`·`state_after`, 그 전 상태를 만든 이벤트 `made_by`, 정정들 `corrected_by`, 상태가 없을 때 `reason`). 본문 상한 오류 `text_too_long`(본문 UTF-8 32 KiB `TEXT_LIMIT` 초과, 또는 직렬화한 줄(끝 LF 포함)이 50 KiB `LINE_LIMIT` 미만이 아님. `details`: `text_bytes`·`line_bytes`·`text_limit`·`line_limit`, 아무것도 쓰지 않고 자르지 않음, OQ2). kind 입력 `gap`은 `bad_kind`(`details.use: workflow_feedback`) |
| 저장이 끝나지 않은 기록 | `recording_status`: `complete` / `incomplete`(사유 `not_final`·`no_completion_event`·`finalize_errors`) / Report 저장 준비 단계의 `prepared`. `execution_status`·`render_status`와 별개 |
| 읽지 못한 기록 | `unsupported`: 모르는 가족·버전·kind(사유 `unknown_family`·`unknown_version`·`unknown_kind`)와 참조의 모르는 type·relation·basis·version_binding. 보존하고 셈. `damaged`: 파싱할 수 없는 줄. 줄 번호와 함께 셈. 조용히 건너뛰지 않음 |
| 쓰기를 허용하는 kit | 표식의 `min_writer_version`. 그보다 낮은 kit의 쓰기는 `writer_too_old`로 거절하고 읽기만 허용하며 필요한 버전을 안내 |
| 프로젝트 ID | 표식의 `project_id`. 복사본도 같은 값이므로 계통이며 유일성의 보장이 아님 |
| 기록의 `path`, `external` | `path`는 정체성 경로: 프로젝트 상대 POSIX 경로, 철자는 파일 시스템에 저장된 이름, 밖을 가리키는 링크(디렉터리·파일)는 경계. `external`은 정체성이 프로젝트 밖일 때만 참. 보안 판정은 끝까지 푼 경로로 따로 함 |
| 옛 kit의 경로 대응 | kit 0.5.0 전 run이 기록한 경로(루트·링크 대상 아래의 절대 경로, 저장된 철자가 아닌 상대 경로)를 지금의 정체성 경로로 읽은 것. 추론(`basis: inferred`)이며 관측·확정 연결이 아님. 생산자 판정의 `read_as`·`inferred_candidates`, 참조 판정의 `inferred_candidates`, `status` JSON의 `inferred`·`inferred_inputs`·`inferred_upstream`·`inferred_versions`. 화면은 `inferred from an older kit's path`(Workspace 배지 `Inferred from an older kit's path`). 쓰는 시점의 결박인 `version_binding: inferred`와 다른 축 |
| 줄 끝만 다른 변경 | `changed`의 사유 `line_endings_only`. 바이트 해시와 상태 `changed`는 그대로이며 같음으로 바꾸지 않음 |
| Report 파일 경로 | `content/` 안의 POSIX 상대 경로. 구성 요소마다 세 OS에서 같은 이름, NFC, 대소문자를 무시해도 서로 다름. 어기면 `report_path_invalid` |
| 새 기록 종류 | `analytics.event/2`의 kind `metric_definition`, `reuse_notification`(재사용 후보를 보인 사실, `trigger`: requested·automatic), `reuse_mode`(제안 모드의 변경, `scope`: project·session), `report_revision`(Report 저장의 완료), `publication`(게시본 build), `publish_receipt`(전달 결과). Report 버전 manifest의 가족은 `analytics.report_revision/1` |
| 프로젝트 표식의 판정(kit 0.7.0) | 디렉터리의 두 표식 `analytics.json`·`lab.json`으로 정함: `none`(표식 없음), `legacy`(`lab.project/1`, `schema` 없거나 JSON 객체가 아닌 `lab.json` 포함), `current`(`analytics.project/2`), `unknown`(다른 가족·버전), `invalid`(표식이 아닌 `analytics.json`, 또는 읽지 못한 표식), `conflict`(두 표식). 가장 가까운 표식의 디렉터리가 프로젝트. 쓰기의 거절: `project_too_new`(이 kit이 쓰지 않는 표식: 모르는 가족·버전. kit 0.7.0에서는 이 kit이 읽고 쓰지 않던 `analytics.project/2`도), `marker_invalid`(`details.reason`: `unreadable`·`unparsable`·`not_object`·`missing_field`·`bad_version`), `marker_conflict`(읽기도 거절, 자동으로 고르지 않음), `no_project`. `status` JSON의 `project.marker`·`format`·`writable`·`write_refusal`과 읽기가 건너뛴 부수 쓰기 `writes_skipped`(값 `hash_cache`), 글의 `read only:` 줄 |
| 옛 형식 프로젝트의 쓰기 차단(kit 0.8.0, #13의 셋째 PR ③) | `legacy` 프로젝트(`lab.json`)의 일반 쓰기 거절 `migration_required`(`details`: `marker`·`format`·`operation`·`command`; hint는 `analytics migrate --to <새 빈 폴더>`, 미리 보기 `--dry-run`, 원본 그대로, aelix에서는 사람이 터미널에서 부른 뒤 `/analytics use`). 부수 쓰기는 건너뜀. `status` JSON의 `project.migration_hint`(그 안내 글), 글과 맥락 블록 머리의 `Older record format: read only here until a person migrates it (analytics migrate --to DEST).`. `current` 프로젝트 위쪽 디렉터리의 `lab.json`(kit 0.7.0 전 kit은 그쪽에 씀)은 `status` JSON의 `project.older_project_above`와 글의 `warning:` 줄로 알리고 거절하지 않음. init의 위쪽 표식 거절은 전환과 같은 `destination_invalid`(`details.reason: inside_project`, OQ9) |
| 기록의 작성 출처 | 진입점이 정해 넘기는 `Origin`: `actor`(누가 쓰거나 요청했나), 실행 수단 `means`(`cli`·`sdk`·`tool`·`command`·`hook`), 작성 주체의 근거 `actor_basis`(`declared`: 진입점·사람이 밝힘, `inferred`: 밝히지 않아 kit 기본값), `session`, `request_id`. `means`·`actor_basis`는 `analytics.event/2`·`analytics.run/2`의 선택 필드 이름이며(기록 schema), 공통 writer(`writer.Writer`)가 기록한다. kit CLI가 받은 `AELIX_ANALYTICS_MEANS`가 닫힌 목록 밖이면 `bad_means`(kit 0.8.0)로 거절한다. `via`(run 입출력이 어떻게 생겼나)·`channel`(게시 주소)·`surface`(알림 표면)·`entry`(Report 진입 파일)와 다른 축 |
| 프로젝트 전환(kit 0.7.0, #13의 둘째 PR ②) | `lab.json` 프로젝트를 새 폴더에 새 형식으로 복사하는 일(원본은 그대로). kit 0.7.0에는 전환 엔진만 있었고 kit 0.8.0부터 명령 `analytics migrate`(§2)로 연다. 명령의 응답은 엔진의 것이고 미리 보기(`--dry-run`)의 계획에는 쓰지 않고는 판정하지 않는 두 가지 `folding_judged`·`publication_judged`(늘 `false`)가 있다. 2단계 시험의 ledger 필드: `start`의 `probe_publish`(시험 이름), `probed`의 `publish`(표식을 게시할 포트 메서드 `publish_new`·`rename_new`). 목적지의 전환 기록 `.analytics/migration/<migration_id>/`(`migration_id`: `<UTC yyyymmddThhmmssZ>-<무작위 8자>`)와 그 ledger `progress.jsonl`(줄 종류 `step`: `start`·`probed`·`locked`·`plan`·`begin`·`direct_intent`·`direct`·`copied`·`source_checked`·`verified`·`transform_begin`·`transformed`·`reported`·`durable`·`marker_begin`·`committed`·`trusted`·`failed`), 계획 `plan.json`(다시 세운 계획 `plan-<n>.json`), 매핑 `mapping.json`, 보고서 `report.json`·`report.md`, 제안 `proposals/`, 원본 표식의 바이트 `source-marker.json`, 목적지 잠금 `.analytics/migration.lock`. 표식의 선택 필드 `migration`(`id`·`report`·`report_sha256`). 커밋 전의 목적지는 표식 판정 `incomplete`(표식 없이 전환 기록이 있음)이고 모든 kit 명령이 `migration_incomplete`로 거절(읽기 포함, `details`에 ledger의 진행). 전환의 오류: `source_not_legacy`(`details.state`), `nested_project`, `destination_invalid`(`details.reason`: `overlaps_source`·`not_empty`·`inside_project`·`not_a_directory`·`name_collision`·`managed_link`, kit 0.8.0의 `no_exclusive_rename`: 목적지 파일 시스템이 링크도 이미 있는 이름을 바꾸지 않는 rename도 주지 않아 표식을 한 번에 게시할 수 없음, 복사 전 2단계의 시험이나 표식 전 11단계에서), `destination_busy`, `source_busy`, `source_changed`, `source_unreadable`(`details.reason`: `permission`·`in_use`·`link_target_missing`·`name_not_utf8`·`os_error`), `destination_mismatch`(`details.reason`: `occupied`·`unaccounted`·`invalid`), `destination_write_failed`, `insufficient_space`; 다시 부른 전환의 `already_migrated`. 보고서의 `excluded` 사유(`environment`·`workspace`·`cache`·`lock`·`os_metadata`)와 `not_copied` 사유(`git_worktree_pointer`·`special_file`·`link_to_root`·`link_to_managed_directory`·`inside_link_directory`·`target_missing`·`copied_elsewhere`·`link_not_made`), 링크의 처리 `copied_as_file`·`copied_as_directory`, 경고 `long_path`·`unportable_name`·`marker_rewritten`, `leftover_temp`. `status` JSON의 `project.migration`(`id`·`report`·`mapping`·`applied`, 적용하지 않았으면 `reason`: `invalid`·`report_missing`·`report_changed`·`mapping_missing`·`mapping_changed`·`unreadable`) |

표의 '근거 버전의 고정 방식' 행부터 '새 기록 종류' 행까지는 [ADR-0006](decisions/0006-common-record-contract.md)과
[ADR-0007](decisions/0007-line-endings.md)이 채택한 기록 계약의 이름이다. kit 0.8.0의 공통 writer가 그 가운데 `analytics.event/2`의 결박
(`version_binding`·`relation`·`basis`·`digest`), `request_id`, `state_conflict`를 쓴다. 나머지(새 기록 종류, Report, 게시)는 그 기능의 Issue가 쓴다.
읽기의 이름 — 해석 상태, `confirmed`, `recording_status`, `unsupported`·`damaged`, `source_revision`·`as_of_seq`·`updating`, 옛 kit의 경로 대응 — 은 #12의 읽기 모델이
현재 기록(`lab.*/1`)을 읽어 `analytics_status`와 Workspace에 보인다([현재 계약](CONTRACTS.md) E). '줄 끝만 다른 변경'의 사유
`line_endings_only`는 kit 0.4.0([#42](https://github.com/handochan/aelix-analytics/issues/42))부터 현재 기록의 입력·코드 상태를 읽을 때
붙는다(기록에 쓰는 필드가 아니라 읽기의 사유, E). 화면의 글은 `(line endings only)`·`Line endings only`다.
'프로젝트 표식의 판정'과 '기록의 작성 출처' 행은 #13의 첫 구현 PR(kit 0.7.0)이 정한 이름이다. 판정·거절·`status`의 필드는 지금
동작이고([현재 계약](CONTRACTS.md) B·D), `means`·`actor_basis`는 진입점이 넘기는 `Origin`에 있고 kit 0.8.0(#13의 셋째 PR)부터 모든 진입점의 쓰기가 공통 writer로 기록에 쓴다.
'공통 writer' 행과 '쓰기의 응답과 본문 상한' 행, `state_conflict`의 `details`, '옛 형식 프로젝트의 쓰기 차단' 행, §2의 '프로젝트 전환 명령' 행, 전환 엔진의
`rename_new`·`RenameUnsupported`·`no_exclusive_rename`·`publish`·`probe_publish`·`publication_judged`는 #13의 셋째 PR이 정한 이름이다.

자동 맥락에 포함되지 않은 부분은 Omitted from current context로 표현하고 더 읽을 위치를 제공한다. 실제 잘린 과거 응답은 Truncated라는 사실을 숨기지 않는다.
상태를 보여 줄 때 대상과 범위도 함께 보인다. 예를 들어 `Run succeeded · Recording incomplete · Rerun comparison not run`처럼 각각의 사실을 읽을 수 있어야 한다.
사람에게는 Version 1처럼 읽기 쉬운 번호를 보여 주되 주소·참조는 안정된 ID를 사용한다. 형식 버전·코드 버전·내용 버전·게시본 버전을 합치지 않는다.

## 7. 영어 기본값과 다국어 내용

**시스템이 제공하는 인터페이스·템플릿·속성은 영어를 기본으로 한다. 대화 언어와 분석 내용의 언어는 별도로 존중한다.**

| 계층 | 기본 규칙 |
|---|---|
| 탭·메뉴·버튼·속성·빈 화면·오류·접근성 label | English. 대화가 한국어라는 이유로 UI가 자동 전환되지 않음 |
| 코드·API·JSON keys·enum·URL anchors·설정명 | 영어의 안정된 식별자. UI 번역과 무관하게 유지 |
| 기본 CONTEXT·보고서 예제·데모·SDK 예제 | 영어 제목·안내·placeholder. 새로 작성하는 내용은 사용자 언어 사용 가능 |
| 목적·질문·발견·차트 제목·보고서 본문 | 작성 언어 보존. 한국어 세션에서는 한국어로 작성 가능하며 언어를 강제하지 않음 |
| 원본 데이터 열 이름·값·파일명·보존 코드·과거 기록 | 번역·영문화·ASCII 치환을 일괄 수행하지 않음 |
| 프로젝트 locale / 게시 locale | 설정할 경우 서로 명시적으로 구분. 게시본의 UI locale은 빌드에 고정하고 원래 보고서 언어와 구별 |
| 저장소 설명·연구·과거 검토 문서 | 이번 정책으로 일괄 번역하지 않음. 사용자용 기본 템플릿과 개발 과정의 한국어 문서는 다른 대상 |

기본 탭은 `Overview`, `Insights`, `Reports`, `Visualizations`, `Analysis map`, `Data`, `Runs`, `Timeline`, `Context`다.
속성은 `Created at`, `Based on`, `Version`, `Status`, `Evidence`, `Scope`처럼 표시하고, 기계 키는 `created_at`처럼 유지한다.
기록 상태 구역은 `Record coverage`, 도구 사용은 `Tool usage`, 사용 중 문제는 `Workflow feedback`으로 표현한다.
새 섹션·메뉴를 용어마다 만들라는 뜻은 아니다. Metrics 등은 기존 점진적 공개 설계의 위치를 따른다.

UI 문자열은 안정된 message key와 영어 기본 카탈로그를 통해 관리한다. 누락한 번역은 영어로 대체하며 언어별 도구 schema 복제는 하지 않는다.
HTML의 `lang`은 실제 UI 언어에 맞추고, 알려진 한국어 본문은 해당 영역의 언어로 표시한다. 언어를 모르면 임의 추정으로 기록하지 않는다.
날짜·수치 표시는 locale과 시간대·단위를 구별한다. 영어 UI라고 데이터 시간대를 바꾸지 않고, 시각의 offset·단위를 잃지 않는다.
1024 기반 용량 표시라면 KiB/MiB, 1000 기반이면 kB/MB처럼 계산 기준과 label을 맞춘다.
CJK 폰트·Unicode 경로·한국어 검색·접근성 지원을 영어 기본화 과정에서 제거하지 않는다.

새 CONTEXT 템플릿([`template/CONTEXT.md`](../src/aelix_analytics/template/CONTEXT.md), #10부터)의 섹션 제목과 안내 줄이다(머리말,
`Data sources`의 표, 빈 `- ` 항목, 끝의 만든 날짜 주석은 생략). 짧은 영어 머리말(파일의 역할, `pin`, journal과의 구분)이 첫 `##` 제목
위에 있다:

```markdown
# {{title}}

## Goal <!-- pin -->
{{goal}}

## Analysis focus <!-- pin -->
- (Current questions, the metrics they look at, definitions still open)

## Next steps <!-- pin -->

## Current findings
- (Confirmed findings and decisions, with the run ids they rest on and their limits)

## Definitions and rules
- (Metric definitions, filters and exclusions, units, periods)

## Data sources

## Assumptions and limitations
```

이전 방향 예시와 두 가지가 다르다. `Analysis focus`는 옛 템플릿의 `핵심 질문 <!-- 항상 -->`을 이어 고정한다(현재 질문이 늘
프롬프트에 들어가는 동작을 언어 전환으로 바꾸지 않음). 안내는 HTML 주석이 아니라 `(`로 시작하는 줄이다. Workspace는 CONTEXT의
HTML을 그리지 않아 주석이 글자로 보이고, 목표를 읽는 쪽은 `(`로 시작하는 줄을 안내로 보고 건너뛴다.

빈 섹션은 분석을 막는 필수 양식이 아니다. 내용을 한국어로 쓰거나 자유 섹션을 추가할 수 있다.
기존 `## 목표`, `<!-- 항상 -->` 문서도 읽으며 과거 문서를 영어로 덮어쓰지 않는다. pin은 맥락 선택 힌트이며 토큰 강제 한도나 별도 모델 호출 설정이 아니다.
#10 전의 kit은 `<!-- pin -->`을 모르므로, 새 템플릿의 프로젝트를 그 kit으로 읽으면 그 섹션을 고정하지 않고 이름에 표식이 남는다(내용은 예산 안이면
들어가고, 아니면 줄 범위로 목록에 남음).

### 현재 구현(#10)

- 생성한 Workspace(정적 HTML)의 탭·제목·배지·버튼·빈 화면·tooltip·aria-label·오류와 kit의 status 안내는 영어다. 문서의 `lang`은 `en`이다.
  #96부터 읽는 사람이 상단 메뉴로 화면을 한국어로 바꿀 수 있다([현재 계약](CONTRACTS.md#c12-workspace-화면-언어-kit-0160-96)). 바꾸면 `<html lang>`이 `ko`가 되고,
  번역이 없어 영어로 남는 kit 문구 요소에는 `lang="en"`이 붙는다. 속성(title·aria-label)은 언어를 따로 적을 수 없어, 속성에 쓰는 key는
  모두 번역한다.
  사람과 스크립트가 쓴 내용을 담은 요소는 `lang=""`(언어 모름)이다: CONTEXT 본문과 버전 diff, 기록 글, 프로젝트 제목·목표·경로,
  run·Visualization 제목·caption·그림의 대체 글, 데이터셋 이름·경로·설명, 열 이름과 값, 스크립트의 코드·출력·오류 요약, 맥락 섹션
  이름. 프로젝트에 언어를 적는 곳이 없어 알려진 언어가 없으므로 추정하지 않고, 화면 낭독기는 그 글을 영어 음성이 아니라 읽는 사람의
  기본 음성으로 읽는다(#10 전에는 페이지가 `ko`라 한국어 프로젝트의 글을 한국어로 읽었고, 이 접근성을 잃지 않음). kit의 label은 화면
  언어의 범위에 남는다. 사람이 쓴 글은 화면 언어와 관계없이 `lang=""`이고 메시지 값으로 넘기지 않는다. 문서 제목(`<title>`)은 kit의 글과
  사용자 글을 한 문자열로 이으며, 화면 언어를 바꾸면 kit의 글 부분만 바뀐다. CSS 글꼴 목록은 macOS·Windows·Linux의 한국어 글꼴을 포함한다.
- 지금 탭은 `Overview`, `Insights`, `Reports`, `Visualizations`, `Analysis map`, `Timeline`, `Data`, `Runs`, `Context`, `Analysis assets`,
  `Evaluation`이다(`Insights`는 [#18](https://github.com/handochan/aelix-analytics/issues/18), `Analysis map`은 [#98](https://github.com/handochan/aelix-analytics/issues/98),
  `Reports`는 [#19](https://github.com/handochan/aelix-analytics/issues/19) PR3, 한국어 `보고서`).
  `Evaluation`은 옛 '평가' 탭의 이름을 옮긴 것이고, 안의 구역은 `Workflow feedback`·`Record coverage`·`Tool usage`다. 탭의 URL anchor
  (`#charts`, `#evaluation` 등)는 안정 식별자라 그대로다.
- 사람과 스크립트가 쓴 글은 번역하거나 Unicode 정규화(NFC 등)하지 않고 쓴 그대로 보인다. `CONTEXT.md`는 Markdown으로 그린다(HTML은
  그리지 않아 글자로 보임). 그 밖의 글 — 기록 글, 제목, caption, 열 이름과 값, 데이터셋 이름, 경로(NFD 포함) — 은 HTML escape만
  거친다(링크 대상은 퍼센트 인코딩, 페이지의 줄 끝은 LF — [ADR-0007](decisions/0007-line-endings.md) 결정 2, 표 미리보기는 긴 값을
  줄임표로 줄임). 데이터셋은 쓴 그대로의 이름(파일 stem)으로 보인다. SQL view 이름(`[a-z0-9_]`로 바꾼 기계 식별자:
  `My Sales`→`my_sales`, `my-data`→`my_data`, `123sales`→`t_123sales`, 한국어 이름→`___…`)이 그 이름과 다르면 데이터 탭과 데이터셋
  페이지가 view를 옆에 적는다.
- 새 프로젝트의 템플릿(`CONTEXT.md`, uv 프로젝트 이름 `analytics-<slug>`)과 데모(데이터·제목·기록·스크립트)는 영어다.
- 용량은 1024 단위이므로 `KiB`·`MiB`·`GiB`로 적는다. 시각은 기록의 offset 그대로의 벽시계 시각이고 읽는 사람의 시간대로 바꾸지 않는다.
  날짜는 화면 언어의 형식(`Aug 3, 2026 09:10`, `2026년 8월 3일 09:10`)이며, offset을 포함한 원래 값은 `<time>`의 `datetime`에 있다.
- Workspace의 kit 문구는 message key 카탈로그다: `assets/i18n/en.json`이 원본, `ko.json`이 한국어이고, 생성기와 관계 페이지와 스크립트가
  같은 key를 쓴다. status·CLI·SDK·모델 도구의 출력과 게시본의 UI locale은 영어 그대로다(#96 범위 밖). `tests/test_language.py`가
  출하 파일의 한국어(한국어 카탈로그와, 옛 CONTEXT의 목표 제목·고정 표식을 읽는 줄만 허용)와, 한국어 프로젝트의 status·맥락 블록에서
  사용자가 쓴 글 밖의 한국어, 생성 HTML에서 `lang=""` 요소 밖의 한국어를 막는다. `tests/test_workspace_i18n.py`가 두 카탈로그의 key·자리표시자와
  페이지의 key·영어·메시지 값을, Node 시험이 전환·저장소 차단·URL 전달·영어 대체를 확인한다.
- 이미 영어인 표면의 이름(CLI 도움말의 `HTML report`, status 글의 `charts`, 저장 이름 `report/`)은 이 전환의 범위가 아니다(§2·§3).

## 8. 적용과 변경 관리

### #19 채택한 공개 이름

[ADR-0013](decisions/0013-report-storage-and-collections.md)은 2026-10-18 채택했다.
kit 0.21.0은 개별 버전 저장·조회·철회/복원·prepared 정리의 CLI/SDK를 구현한다.
`ReportRenderer`·collection·선택 metadata는 후속 PR이다.

| 이름 | 범위와 기존 개념과의 차이 |
|---|---|
| `analytics reports` | 개별 Report의 저장·목록·정확한 버전 조회·철회/복원·명시적 prepared 정리. Workspace를 재생성하는 `analytics render`와 구별 |
| `analytics.save_report`·`analytics.reports` | 같은 공통 저장/읽기 기능의 SDK. 자유 생성 코드를 실행하거나 전용 상시 모델 도구를 추가하는 이름이 아님 |
| `analytics.update_report`·`analytics.clean_report_prepared` | 정확한 완료 버전의 철회/복원과 지정한 준비 시도의 명시적 정리. 과거 바이트·다른 시도를 고치는 API가 아님 |
| `ReportRenderer` | 이미 보존한 버전을 보기 파일로 바꾸는 주입 가능한 형식별 경계. 생성·저장·공유 범위 판정과 구별 |
| `report_collection/1` | `analytics.report_revision/1`의 선택 `collection.contract` 값. 별도 schema 가족이 아니며, 요청한 전체 구성원의 정확한 버전·실패·미생성 상태를 고정한 묶음 |
| `metadata_file` | 선택 메타데이터를 지문에 결박하는 보존 파일 경로. 사용자 본문이나 재생성 Workspace 파일과 구별 |

### #21 채택한 공개 이름

[ADR-0009](decisions/0009-first-publish-target.md)와 [폴더 게시 설계](design/PUBLISH-FOLDER.md)(2026-10-27 채택)의 이름이다. kit 0.29.0은 대상 폴더의 판정·형식·channel 규칙과
포트 확장만 구현하고, `analytics publish` 명령·게시 기록은 후속 PR이다.

| 이름 | 범위와 기존 개념과의 차이 |
|---|---|
| 게시 대상(publish target) | 사용자가 지정한 폴더 가운데 표식이 있는 analytics 소유 폴더. 프로젝트 안의 build 폴더(`.analytics/publications/`)와 구별 |
| `analytics-target.json`(`analytics.publish_target/1`) | 대상 표식. analytics가 이 폴더를 쓴다는 표시일 뿐 접근 권한이나 팀 범위를 뜻하지 않음 |
| `.analytics-target.lock` | 대상 잠금 파일. 정리하지 않음 |
| channel | 한 프로젝트의 최신 주소 이름(`projects/<channel>/`). Report 묶음(collection)이나 배포 채널과 구별 |
| 버전 주소 / 최신 주소 | `publications/<publication_id>/index.html` / `projects/<channel>/index.html`. 최신 주소는 확인을 마친 버전 가운데 build 순서가 가장 새 것(되돌린 동안은 사람이 고른 것)이며 §6의 Latest(가장 최근 저장본)와 다름 |
| `latest.json`(`analytics.publish_channel/1`) | 최신 포인터의 정본과 그 channel의 history |
| 소유 기록(`analytics.publish_staging/1`) | `.staging/<attempt_id>.json`. 한 게시 시도가 그 staging을 쓰는 중이라는 표시. 기기·호스트는 해시로만 담음 |
| `attempt_id` | `att-`와 소문자 ULID. 게시 시도를 구별할 뿐 재시도 판정에 쓰지 않음(재시도 단위는 `publication_id`·패키지 해시·대상) |
| `publish_attempt` | 게시 시도의 시작 기록 kind(kit 0.30.0). 대상을 바꾸기 전에 씀. receipt 없는 시도는 결과가 기록되지 않은 시도이며 성공이 아님 |
| `analytics publish` | 고정 build를 지정한 폴더에 전달·확인하고 최신 주소를 바꾸는 명령. build(`analytics build`)와 별개이며 분석을 다시 실행하지 않음 |
| `not_newer` / `superseded` | 게시는 끝났으나 최신 주소를 바꾸지 않은 결과: 더 새 build가 아님 / 시작한 뒤 다른 게시가 최신을 바꿈 |

명칭·영어 기본화는 새 공개 표면에서 한 번에 일관되게 제공한다. 구현 PR은 현재 코드·예제·오류 안내·실제 설치물을 함께 갱신한다.
옛 도구를 기본 노출 목록에 남기거나 사용자를 두 가지 용어 체계로 안내하지 않는다.
과거 프로젝트·세션·보존 스크립트는 원래 이름과 바이트를 보존하고 legacy reader·명시적 전환으로 읽는다.
작업 스크립트는 복사본이나 검토 가능한 diff로 새 SDK에 맞춘다. 과거 실행 스냅샷까지 문자열 치환해 원래 실행 증거를 바꾸지 않는다.

명칭 전환과 의미 변경은 구별한다. 예를 들어 `lab_run`의 이름 변경이 실패·재실행 동작을 바꿀 이유가 되지 않는다.
반면 `gap` 종류, `record_update`, timestamp 필드, schema namespace·디렉터리 변경은 reader·writer·참조·전환을 함께 검증한다.
공개 계약의 새 용어는 이 용어집에 뜻·범위·기존 개념과의 차이를 먼저 남기고 관련 예제·오류·화면에 같은 이름을 쓴다.
상세한 이행 순서와 수용 기준은 [점검 기록](REVIEW-TERMINOLOGY-2026-09-18.md), 출시 관리는 [베타 기준](BETA-QUALITY-GATES.md)을 따른다.


#20 PR3의 `analytics.publication/1`은 실제 독립 사본 manifest 가족입니다. `publication_id`는 소문자 pub-ULID이며
`package_sha256`은 manifest 자체, `content_sha256`은 세 정체성 파일을 뺀 내용 파일을 식별합니다.
manifest `ItemKey`는 category와 페이지의 결정적 16자리 hex `key_name`을 쓰고 private snapshot/이벤트의 TypedRef ID와 구별합니다.
`recording_status`와 `folder_status`는 각각 기록 완료와 현재 사본의 관찰이며 `resolved`는 전자의 정체성 판정입니다.
`project_binding_checked=false`인 독립 verify는 원본 기록을 확인하지 않았습니다. 분석 상태는 여전히 declared이며 검사/결론 검토를 대신하지 않습니다.
