# 제품 용어·코드 명칭·기본 언어

작성: 2026-09-18 · 상태: 구현 전 설계. 아래 명칭·경로·API는 전환 목표이며 아직 실행 가능한 현재 명령이 아니다.
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
| Aelix 사람 명령 | `/lab` | `/analytics` |
| CLI / 모듈 실행 | `bin/lab`, `python -m labkit` | `analytics`, `python -m aelix_analytics` |
| Python SDK | `import labkit as lab` | `import aelix_analytics as analytics` |
| 확장 진입 파일 | `extension/aelix_lab.py` | `extension/aelix_analytics.py`; 패키징 이후 `aelix_analytics.extension` |
| 프로젝트 flag | `--lab-project` | `--analytics-project`; CLI 내부 `--project`는 유지 |
| 모델 읽기·SQL·가져오기 | `lab_status`, `lab_query`, `lab_import` | `analytics_status`, `analytics_query`, `analytics_import` |
| 모델 실행·기록·초기화 | `lab_run`, `lab_note`, `lab_init` | `analytics_run`, `analytics_record`, `analytics_init` |
| 사람/SDK의 기록 쓰기 | `lab note`, `lab.note(...)` | `analytics record`, `analytics.record(...)` |
| SDK의 시각화 기록 | `lab.chart(...)` | `analytics.visualize(...)`; 객체와 참조는 `visualization` |
| 프로젝트 화면 생성·열기 | `lab report`, `lab open` | `analytics render`, `analytics open` |
| 독립 공유 패키지·게시 — 제안 | `lab build`, `lab publish` | `analytics build`, `analytics publish` |
| 오류·내부 상태 | `LabError`, `Project.lab`, `Project.report` | `AnalyticsError`, `state_dir`, `workspace_dir` |
| 표시 구현 | `report.py`, `report.js/css`, `ReportRenderer`, `schedule_report` | `workspace` 모듈·자원, `WorkspaceRenderer`, `schedule_workspace_render` |

위 CLI 작업은 Aelix의 `/analytics` 아래에서도 같은 의미를 사용한다. 지원하는 하위 명령은 실제 구현 기준으로 공개한다.
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
| 실행의 시각화 `runs/<id>/charts/` | `runs/<id>/visualizations/` | 새 실행의 저장 경로. 과거 manifest·경로·바이트는 legacy 대응으로 보존 |
| 가져온 데이터 `data/raw/` | `data/imported/` | 가져오며 형식·타입을 변환했을 수 있는 데이터. 외부 원본 파일과 구별 |
| 파생 데이터·실행 코드 | `data/derived/`, `scripts/`, `runs/` | 이름 유지. 파생 데이터라는 이유만으로 언제든 재생성 가능하다고 가정하지 않음 |
| 맥락·사건 기록 | `CONTEXT.md`, `journal.jsonl` | 이름 유지. 현재 맥락과 시간순 사건의 구별 |
| 신뢰 목록 | `analytics-projects.json` | 호스트의 기존 agent 설정 위치 아래. 활성화 신뢰 범위만 의미 |
| schema namespace `lab.*` | `analytics.project`, `analytics.event`, `analytics.run`, `analytics.dataset`, `analytics.trust` | 가족 이름과 형식 버전을 함께 식별. 기존 `/1` 원문을 새 namespace로 덮어쓰지 않음 |
| 제어 환경 변수 | `AELIX_ANALYTICS_*` | `KIT`은 `RUNTIME_PATH`, `PROJECT`, `UV`, `TRUST_FILE` 등은 의미 유지 |
| 실행 환경 변수 `LAB_*` | `AELIX_ANALYTICS_*` | `ACTOR`, `SESSION`, `RUN_ID`, `RUN_DIR`, `PROJECT`, `STYLE`의 역할 유지 |
| 내부 배포 자원 | `core`, `runtime`, `workspace`, `reports`, `publishing`, `templates` | 책임에 따른 이름. `labcore`·`labkit`·`pack`을 새 공개 브랜드로 남기지 않음 |

호스트가 정한 `AELIX_CODING_AGENT_DIR` 등과 외부 라이브러리의 `MPLBACKEND`, `PYTHONPATH`는 확장 소유 이름이 아니므로 바꾸지 않는다.
`LAB_API`처럼 시험 스크립트에서만 쓰는 표식은 공개 설정으로 승격하지 않는다.
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
| Insights | Findings·Reuse proposals·기록 상태를 읽는 화면 | `insight`라는 중복 원본 종류를 자동 신설하지 않음 |
| `reuse_proposal` / Reuse proposal | 함수·모듈·패키지·skill로 발전시킬 재사용 후보 | 데이터에 대한 분석 발견 |
| Skill / Reusable code | 사용·해석·검사 절차 / 실제 계산 구현 | 둘을 한 가지 형식으로 통합하지 않음 |
| `checkpoint` / Analysis checkpoint | 근거를 연결한 작업 구간 요약 | 전체 데이터 스냅샷·트랜잭션 복구 지점 |
| Context / Project memory | 지금 필요한 작업 맥락 / 검색·재개 가능한 프로젝트 지식 | 사용자 개인 메모리, 전체 대화 덤프 |
| `event` / Journal entry / Timeline | 기록된 사건 / journal 항목 / 시간 흐름을 읽는 화면 | 세 이름을 별도 진실 저장소로 만들지 않음 |

기존 `gap`은 과거 Workflow feedback으로 읽고, 새 기록 종류의 전환에서 대응을 명시한다. Findings나 Skill proposals로 자동 변환하지 않는다.
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
| 하나의 Verified·Quality 배지 | Analysis checks / Conclusion review / Record coverage / Tool usage를 별도로 표시 |
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
| `<!-- 항상 -->`, `<!-- always -->` | 새 템플릿은 `<!-- pin -->`으로 맥락 선택 우선순위를 표시. skill의 always-on 모드와 구별하며 구문 전환을 따로 검증 |
| Token budget | Token usage target / Performance target. 측정 목표이며 강제 절단·거절 한도가 아님 |
| Threshold / Cooldown / Timeout / Page size | 실제 제안 조건·알림 간격·실행 제한·조회 크기. 측정 목표와 다른 설정임을 명시 |
| Input tokens / Cached input tokens / Output tokens | provider별 포함 관계를 정규화해 계산. 캐시 입력을 이중 합산하거나 도구 호출 수를 토큰으로 쓰지 않음 |

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

기본 탭은 `Overview`, `Insights`, `Reports`, `Visualizations`, `Data`, `Runs`, `Timeline`, `Context`다.
속성은 `Created at`, `Based on`, `Version`, `Status`, `Evidence`, `Scope`처럼 표시하고, 기계 키는 `created_at`처럼 유지한다.
기록 상태 구역은 `Record coverage`, 도구 사용은 `Tool usage`, 사용 중 문제는 `Workflow feedback`으로 표현한다.
새 섹션·메뉴를 용어마다 만들라는 뜻은 아니다. Metrics 등은 기존 점진적 공개 설계의 위치를 따른다.

UI 문자열은 안정된 message key와 영어 기본 카탈로그를 통해 관리한다. 누락한 번역은 영어로 대체하며 언어별 도구 schema 복제는 하지 않는다.
HTML의 `lang`은 실제 UI 언어에 맞추고, 알려진 한국어 본문은 해당 영역의 언어로 표시한다. 언어를 모르면 임의 추정으로 기록하지 않는다.
날짜·수치 표시는 locale과 시간대·단위를 구별한다. 영어 UI라고 데이터 시간대를 바꾸지 않고, 시각의 offset·단위를 잃지 않는다.
1024 기반 용량 표시라면 KiB/MiB, 1000 기반이면 kB/MB처럼 계산 기준과 label을 맞춘다.
CJK 폰트·Unicode 경로·한국어 검색·접근성 지원을 영어 기본화 과정에서 제거하지 않는다.

새 CONTEXT 템플릿의 방향 예시이며 아직 현재 파일에 반영된 내용은 아니다:

```markdown
# {{title}}

## Goal <!-- pin -->
{{goal}}

## Analysis focus
<!-- Current questions, metrics, and unresolved definitions. -->

## Next steps <!-- pin -->

## Current findings
<!-- Include evidence references and limitations. -->

## Definitions and rules

## Data sources

## Assumptions and limitations
```

빈 섹션은 분석을 막는 필수 양식이 아니다. 내용을 한국어로 쓰거나 자유 섹션을 추가할 수 있다.
기존 `## 목표`, `<!-- 항상 -->` 문서도 읽으며 과거 문서를 영어로 덮어쓰지 않는다. pin은 맥락 선택 힌트이며 토큰 강제 한도나 별도 모델 호출 설정이 아니다.

## 8. 적용과 변경 관리

명칭·영어 기본화는 새 공개 표면에서 한 번에 일관되게 제공한다. 구현 PR은 현재 코드·예제·오류 안내·실제 설치물을 함께 갱신한다.
옛 도구를 기본 노출 목록에 남기거나 사용자를 두 가지 용어 체계로 안내하지 않는다.
과거 프로젝트·세션·보존 스크립트는 원래 이름과 바이트를 보존하고 legacy reader·명시적 전환으로 읽는다.
작업 스크립트는 복사본이나 검토 가능한 diff로 새 SDK에 맞춘다. 과거 실행 스냅샷까지 문자열 치환해 원래 실행 증거를 바꾸지 않는다.

명칭 전환과 의미 변경은 구별한다. 예를 들어 `lab_run`의 이름 변경이 실패·재실행 동작을 바꿀 이유가 되지 않는다.
반면 `gap` 종류, `record_update`, timestamp 필드, schema namespace·디렉터리 변경은 reader·writer·참조·전환을 함께 검증한다.
공개 계약의 새 용어는 이 용어집에 뜻·범위·기존 개념과의 차이를 먼저 남기고 관련 예제·오류·화면에 같은 이름을 쓴다.
상세한 이행 순서와 수용 기준은 [점검 기록](REVIEW-TERMINOLOGY-2026-09-18.md), 출시 관리는 [베타 기준](BETA-QUALITY-GATES.md)을 따른다.
