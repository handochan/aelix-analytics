# 공유 build 설계 — 선택 범위의 독립 HTML·ZIP 패키지

- 상태: **설계 채택·단계별 구현 중.** PR1은 내부 `PublicationProjection`·공유 모델·공통 닫힘·snapshot의 순수 경계를 구현했다.
  PR2–PR3은 `analytics build`·preview·private saved build·`publication.json` 쓰기/결박·패키지 검사·ZIP을 구현했다.
  PR4는 Report·묶음 범위(`--report`·`--scope`·`--collection`·`--allow-withdrawn`, ReportRenderer 주입, `scope.reports`)를 구현했다.
  PR5는 독자 환경의 검증(원본 없는 위치·HTTP 하위 경로·OS 도구 ZIP·브라우저 E2E)과 CSP·frame 결정, M 규모 측정을 했다.
  publish/receipt는 #21, 실제 다른 독자의 열람은 #24다.
  현재 공개 동작은 [현재 계약](../CONTRACTS.md)이 설명한다. Workspace와 별도로 독립 공유 패키지를 생성한다.
- 작업: [#20](https://github.com/handochan/aelix-analytics/issues/20) `[B4-s/P0]`.
  [2026-10-17 순서 완화](https://github.com/handochan/aelix-analytics/issues/20#issuecomment-6232441606)로 PR-0~PR-3은
  [#19](https://github.com/handochan/aelix-analytics/issues/19) 앞에 시작할 수 있다. Report 범위 PR-4는 #19 뒤다.
  #13·#18은 병합됐으며 현재의 writer(`analytics.event/2`)·`project_id`·`.analytics/`를 기준으로 구현한다.
- 기준: 코드 줄 번호는 `6c4c2cd` 기준이다. 구현 PR은 그때의 코드와 이 문서를 다시 대조한다.
- 따르는 결정: [공유 설계](../PUBLISH-AND-SHARING.md)(아래 PUBLISH), [ADR-0009](../decisions/0009-first-publish-target.md),
  [ADR-0006](../decisions/0006-common-record-contract.md), [ADR-0001](../decisions/0001-evolvable-platform-boundaries.md),
  [ADR-0003](../decisions/0003-portable-boundaries-and-in-process-kit-loading.md), [ADR-0004](../decisions/0004-minimal-distribution-and-entry-points.md),
  [ADR-0005](../decisions/0005-names-first-storage-with-the-new-writer.md), [베타 기준](../BETA-QUALITY-GATES.md) Q14·Q16,
  [용어집](../TERMINOLOGY-AND-LANGUAGE.md), [보고서 설계](../REPORT-ARTIFACTS.md), [아키텍처](../ARCHITECTURE.md).
- 소유자 결정: binding 문서가 정하지 않은 제품 결정 25개(15절, OQ1–OQ25)는 2026-10-07 소유자가 **권장안을 모두 채택**했다
  ([#20 코멘트](https://github.com/handochan/aelix-analytics/issues/20)). 본문에서 "(OQn)"이 붙은 규칙은 그 결정이다. OQ15(새 기록 필드
  `content_seq`)와 OQ20(ADR-0006 §4에 없는 판정)은 [ADR-0014](../decisions/0014-publication-record-contract.md)에
  구체화했다(Accepted, 2026-10-18). 새 공개 이름은 용어집 §8대로 먼저 등록한다.
- 리뷰: `7010c6e`의 설계 리뷰(독립 1·Codex 1)를 반영했다. 지적과 처분은 PR 본문의 표에 있다.

## 0. 요약

1. build는 Workspace(`report/`)를 복사하거나 거르지 않는다. 기록을 `relations.read`로 한 번 읽어 만든 스냅샷에서, 허용 목록에 있는 필드만
   공유 모델로 옮긴다(허용 목록 투영). 목록에 없는 필드와 나중에 기록에 생긴 필드는 기본으로 빠진다.
2. 페이지를 그리는 renderer는 공유 모델만 받는다. 계층 검사가 renderer의 기록·프로젝트·플랫폼 모듈 import를 막는다.
3. 패키지의 모든 바이트는 세 출처 가운데 하나다: kit이 공유 모델로 만든 글, kit·라이브러리의 번들 자원, 복사 계획에 있는 원본 파일의
   그대로 사본. 사본은 내용 주소 이름(`files/<sha256>.<ext>`)을 쓰고, Report 파일만 manifest 경로를 지킨다. kit이 정하는 이름은 기록
   정체성에서 직접 만들지 않는다(7.2).
4. build는 투영의 원장을 믿지 않는다. Strategy 밖의 공통 닫힘(5.2)과 대조해 넘으면 멈춘다. 누출 검사는 패키지의 모든 파일을 magic byte로
   판정한 형식별로 풀어 본다. 금지 목록은 공통 코드가 "스냅샷 − (원장 ∩ 공통 닫힘)"과 로컬 환경 값으로 만든다. 풀 수 없는 파일은 담지 않거나
   build를 멈춘다(11.5, OQ23). 걸리면 build는 끝나지 않고 결박도 없다. 빠져나가는 길은 항목 제외(`--exclude`)와 새 Report 버전이다.
5. 과거 근거는 기록으로만 읽는다. 기록된 해시가 있는 대상은 해시가 같은 바이트만 복사한다. 버전 없는 참조를 최신 버전으로 잇지 않는다.
6. manifest의 `basis.as_of_seq`는 ADR-0006 §5의 뜻(이 읽기가 본 마지막 seq) 그대로 정체성 파일에만 둔다. 띠와 내용 비교는 새 필드
   `content_seq`(게시 계열이 아닌 마지막 줄의 seq, OQ15)를 쓴다. 그래서 같은 기록으로 다시 만든 build는 내용 파일의 바이트와
   `content_sha256`이 같다.
7. 쓰는 순서는 파일 → 검사 → manifest(마지막 파일) → `publication` 이벤트다. 이벤트가 커밋 지점이고 결박 없는 폴더는 게시되지 않는다
   (ADR-0009 결정 6). 결박 단계를 시작한 뒤에는 폴더를 지우지 않는다(8.4). 새 이름으로 옮기는 `AtomicFiles.rename_new`는 ADR-0009대로
   #21에 남긴다.
8. 한 프로젝트의 build는 읽기부터 결박까지 잠금으로 직렬화한다. 그래서 build 순서(결박의 seq)와 기록 기준의 순서가 어긋나지 않는다.
9. 모든 페이지에 JS 없이 읽히는 스냅샷 띠가 있다: 기록 시점, CONTEXT 버전, 선언한 분석 상태, 범위. "latest"·"current"·"verified"라고
   쓰지 않는다. `publication_id`·빌드 시각은 `publication.json`·`about.html`·`assets/identity.js`에만 있다.
10. 패키지는 자기 밖을 부르지 않는다. 출하 JS에 네트워크 API가 없고 브라우저 저장소·live reload·`?v=`가 없다. 스크립트나 원격 참조가 있는
    사용자 HTML·SVG는 넣지 않는다(OQ8).
11. 시험의 중심은 표면 × 프로필 × 포함/제외의 canary 행렬과, 원본 프로젝트를 지운 임시 위치(한국어·공백이 든 경로)에서 `file://`과 루프백
    HTTP 하위 경로로 패키지를 순회하는 정적 크롤러(모든 CI 레그)다. 네트워크를 막은 브라우저 E2E는 Linux Chromium이 필수다.
12. PR은 여섯이다: 지금의 결정 PR(PR-0), 투영, 결박 없는 패키지·검사·`--preview`, build와 결박(기록 형식 PR), Report 범위, ZIP과 독자 E2E.

## 1. 범위와 전제

### 1.1 맡는 것과 맡지 않는 것

| #20이 맡는다 | 맡지 않는다 |
|---|---|
| `PublicationProjection`의 두 구현(`read`·`evidence`)과 계약 시험(ADR-0001 확인 2) | 대상 폴더로의 전달, 최신 주소, `--switch-to`, receipt(#21, ADR-0009) |
| 프로젝트 읽기 화면, 선택한 Report 버전, Report 묶음 버전의 공유 범위 | 실제 다른 독자의 열람(#24. 소유자의 다른 기기·계정) |
| 독립 폴더와 ZIP, manifest, 결박 기록, 참조 type `publication`의 읽기 | 독자 기기에서 버전 전체를 대조하는 수단(#48, 베타 뒤) |
| 누출 검사, 링크 규칙, `file://`·HTTP 하위 경로·JS 꺼짐·누락·외부 의존의 검증 | 프로젝트 인계 프로필, 자동 갱신, 피드백(PUBLISH P2·P3) |
| 독자 쪽 "이 사본에 없음" 표시 | 검색 인덱스(OQ21), 가린 사본(OQ13), 공유 전용 가공 산출물(OQ9) |

### 1.2 선행과 고정 입력

- **#13**: `analytics.event/2` writer와 `publication` kind, `project_id`, `.analytics/`(ADR-0005 결정 2). 결박은 #13 없이 쓸 수 없다.
- **#18**: Insights, 관계(1-hop)와 텍스트 대안, 정확한 버전으로의 복귀(URL anchor와 뒤로 가기). 공유본은 같은 형태를 공유 모델에서 다시 만든다.
  관계 계산이 `report.py` 안에 있으면 PR-1이 읽기 모델 쪽 순수 함수로 옮긴다(16절 R7).
- **#19**: Report 버전 writer(`files[].sha256`·`based_on`·`entry`), 상태(`current`·`withdrawn`), 형식별 보기(`ReportRenderer`), 묶음 형식.
  읽는 쪽 판정은 이미 있다(`relations.py:797` `report_revisions`).
- **#14**: 지표 정의·분석 초점. 없으면 CONTEXT 버전과 기록만으로 방향을 보이며 막지 않는다.
- **#41**: S/M 규모 fixture 생성기와 사례(`display_safety`, `unpreserved_data`, `same_chart_id` 등). `/2` 생성은 #13 뒤다.
- **ADR-0009 결정 9**: manifest 내용, 결박, 참조 규칙, 이식 가능한 이름, `publication_id` 조건. 결정 4: 버전 디렉터리에 게시 정보를 넣지 않는다.
  결정 5: build 순서는 `publication` 기록의 journal seq다.
- **ADR-0001**: 공유 범위 투영은 Strategy 경계다(결정 3). 공유본은 원본의 버전 있는 읽기 모델로 만든다(결정 5). 게시 형식은 자기 버전을 가진다(결정 6).
  정책은 진입점이 검토한 설정과 등록된 구현 ID로 조립한다("OS마다 다른 구현의 기본값"의 예외에 들지 않는다).
- **ADR-0003**: 포트는 넷이고 새 포트를 만들지 않으며 호출부에 플랫폼 분기를 두지 않는다. **ADR-0004**: wheel에 의존성이 없고(결정 1) 자원은
  resource API로 찾는다(결정 5). 런처는 SIGTERM을 `run`에만 전한다(결정 7).
- **ADR-0006**: 정체성(§2), 참조와 `not_included`(§3·§4), 읽기 기준(§5), 커밋 규칙과 상태 재생(§6), Report 골격(§7), 경로 정체성(§9).

## 2. 누출 모델

### 2.1 제외한 내용

1. **범위 밖 기록과 그 흔적**: 포함하지 않은 이벤트·run·Visualization 버전·데이터셋·코드·로그·환경·telemetry·Report 버전. 본문뿐 아니라
   ID·이름·경로·제목처럼 개수 이상의 식별 정보도 포함한다(PUBLISH §4 "공개되는 표시명·URL·매핑", §5 "tooltip").
2. **로컬 환경 정보**: 프로젝트 절대 경로, 홈 디렉터리·사용자 이름, venv 경로, aelix session, `source_revision`(같은 기계에서만 뜻이 있는 토큰,
   ADR-0006 §5), 게시 대상 경로.
3. **숨긴 표현**: CSS로 숨긴 카드, 접힌 `<details>`, 같은 그림의 중복 payload. 화면에 안 보여도 바이트에 있으면 공유한 것이다(PUBLISH §5).

### 2.2 새는 길

| 길 | 예(지금 Workspace) | 막는 곳 |
|---|---|---|
| 모델: 기록 필드가 글·속성·payload로 | Overview의 프로젝트 절대 경로(`report.py:850`), 경로 `title` tooltip | 허용 목록 투영(5.3), 순수 renderer(3절) |
| 바이트: 복사한 원본 안의 내용 | 코드 주석의 경로, plotly figure의 `customdata`·`meta`, PNG 텍스트 청크, SVG 주석, Parquet 메타데이터, Report 본문 | 복사 계획의 역할 제한(4.3 C10), 형식별 검사(11절) |
| 이름: 파일 이름·anchor·ZIP 메타 | `runs/<run_id>.html`의 스크립트 slug, 키의 해시로 만든 차트 anchor | kit이 만든 이름 `<k>`와 내용 해시로만 이름 생성(7.2), 결정적 ZIP(9.6) |
| 실행: 스크립트가 실행 중 만드는 참조 | Workspace의 `assets/build.js` 폴링, plotly 지도 타일, altair의 CDN | 정적 참조만 쓰는 독자 JS(7.4), 스크립트·원격 참조가 있는 사용자 HTML 제외(7.5, OQ8) |

### 2.3 원칙

1. **허용 목록 투영.** 공유 모델의 필드는 프로필마다 적은 표(5.3)에 있는 것뿐이다. 기록 dict를 통째로 모델에 넘기지 않는다.
2. **단일 바이트 통로.** 패키지 파일은 writer만 쓰고, 파일마다 출처(`generated`·`bundled`·`copied`·`identity`)를 manifest에 적는다.
3. **순수 renderer.** renderer는 공유 모델을 받아 `{패키지 경로: 바이트}`를 돌려준다. 기록·프로젝트·파일 시스템을 보지 못한다.
4. **독립 검사.** 검사는 Strategy의 판단을 믿지 않는다. 원장을 Strategy 밖의 공통 닫힘과 대조하고, 금지 목록을 스냅샷 전체에서 만들며,
   패키지의 모든 파일을 형식별로 푼다. 풀 수 없는 바이트를 조용히 통과시키지 않는다.
5. **과거 근거는 기록으로만.** 지금 파일과의 비교는 "build 때의 관측"으로 표시한다(PUBLISH §3).
6. **결박은 마지막.** 검사를 통과하고 manifest까지 쓴 폴더에만 `publication` 이벤트를 쓴다.
7. **스스로 설명하고 바뀌지 않는다.** 모든 페이지가 JS 없이 시점·범위·성격을 말하고, 독자 기기에 흔적을 남기지 않는다.

### 2.4 목표가 아닌 것(한계로 적는다)

- 포함한 사용자 글이 제외한 대상을 **바꿔 말한** 경우. 검사는 그대로 옮긴 식별자와 문장만 찾는다.
- 포함하기로 고른 코드·본문 속의 비밀(API 키 등) 탐지. P0 밖이다(OQ12).
- 이미 전달된 ZIP·사본의 회수(PUBLISH §6).

### 2.5 대안과 선택

| 대안 | 검토 결과 |
|---|---|
| `report/`를 복사하고 탭·구역을 숨김 | PUBLISH §2가 배제했다. 숨긴 내용이 바이트에 남는다 |
| `report.py`에 공유 모드 분기를 더함 | 제외가 거부 목록이 된다. Workspace에 새 구역이 생기면 기본으로 공유본에 들어간다 |
| 렌더한 HTML을 정규식으로 지움 | entity·JSON escape·퍼센트 인코딩·속성·payload를 모두 다뤄야 하고 지운 흔적이 남는다 |
| 투영만 하고 검사 없음 | 사용자 글과 복사한 원본 바이트 속의 언급을 막지 못한다 |
| 검사만 하고 투영 없음 | 모든 build가 실패하고 무엇을 고쳐야 하는지 알 수 없다 |
| **허용 목록 투영 + 별도 renderer + 독립 검사(채택)** | 기본이 "안 들어감"이고, 투영의 실수(닫힘을 넘게 잡은 원장 포함)는 공통 닫힘 확인과 독립 검사가 잡고, 검사의 사각(바꿔 말하기, 풀지 못하는 인코딩)은 한계로 적는다. 비용은 Workspace와 따로 도는 renderer다(16절 R7) |

## 3. 파이프라인과 모듈 배치

```text
analytics build
 0  잠금      .analytics/publications/.lock 을 FileLocks로 잡고 결박까지 유지한다(9.5)
 1  CONTEXT   (OQ19) render처럼 core.sync_context로 CONTEXT.md의 새 버전을 남긴다
 2  읽기      relations.read 한 번. updating이면 records_updating으로 끝낸다
 3  스냅샷    ShareSnapshot: 기준(as_of_seq·content_seq·context_version), 재생한 상태, 참조 판정, Report manifest와
             content 판정, 미리 계산한 digest 표(4.1)
 4  투영      PublicationProjection.project(snapshot, request) → Projection(model, copy_plan, ledger)
             → 공통 닫힘 확인(ledger.included ⊆ scope_closure, 5.2) → 모델 단계 누출 검사(Text.source, 11.6)
 5  폴더      .analytics/publications/<publication_id>/ 를 배타적으로 만든다
 6  쓰기      loader(텍스트 원본을 읽고 해시 대조) → renderer(순수) → writer(배타적 생성, 복사하며 해시 대조)
 7  검사      링크 규칙·이름·파일 일치·형식 판정·누출(11절). 실패하면 폴더를 지우려 시도하고 위치를 알린다
 8  manifest  publication.json을 마지막 파일로 쓴다
 9  결박      journal 잠금 안에서 publication 이벤트를 쓴다. 커밋 지점이다. 시작한 뒤에는 폴더를 지우지 않는다(8.4)
10  ZIP       (--zip) 결박과 대조한 폴더에서 결정적 ZIP을 쓴다
```

`--preview`는 아무것도 쓰지 않는다. 잠금, CONTEXT 버전, 폴더, manifest, 결박이 없다. 1단계 대신, `CONTEXT.md`가 기록된 마지막 버전과
다르면 build가 남길 버전을 메모리에서 만들어 기준으로 삼는다. 그다음 2–4단계를 그대로 하고, renderer의 메모리 출력과 복사 계획의 원본을
digest 표와 대조하며 읽은 바이트에 7단계와 같은 검사 함수를 돌린다(검사 함수는 폴더가 아니라 `{패키지 경로: 바이트}`의 보기를 받는다).
그래서 같은 기록이면 미리보기의 포함 목록·크기·검사 결과가 build와 같다(PUBLISH §5 "미리보기에서 파일·데이터 범위·제외 항목·대략적인
크기를 확인"). 다른 것은 정체성 값과, 대기 중인 CONTEXT 버전이 기록될 때 정해지는 값(버전 이름, 기록 시각, 그 `context` 이벤트의 seq)뿐이다.
미리보기와 build 사이에 기록이 더해지면 기준이
달라진다. 그래서 두 출력 모두 기준(`as_of_seq`·`content_seq`, CONTEXT 상태 `recorded`·`pending`)을 적어 사람이 비교할 수 있게 한다.

| 모듈(제안) | 계층(제안), 수준 | import해도 되는 계층 | 외부 | 하는 일 |
|---|---|---|---|---|
| `publishing/model.py` | `share_model`, 0 | 없음 | stdlib | 공유 모델의 frozen dataclass. 기록 dict를 담을 필드가 없다 |
| `publishing/projection.py` | `publication_policy`, 1 | `read_model`, `share_model`, `publication_policy` | stdlib | `PublicationProjection` Protocol, `ShareSnapshot`·`ShareRequest`, `read`·`evidence` 구현과 등록표, `content_seq` 규칙 |
| `publishing/scope.py` | `publication_policy`, 1 | `read_model`, `share_model`, `publication_policy` | stdlib | 공통 닫힘 `scope_closure`(5.2), 구현 ID별 프로필 표, 금지 목록 조립(11.4). Strategy 구현이 아니다 |
| `publishing/checks.py` | `publication_policy`, 1 | `read_model`, `share_model`, `publication_policy` | stdlib | 링크 규칙, 이름, 형식 판정(magic byte, 11.5), `verify_package`(폴더·ZIP), 누출 대조기, 사용자 HTML·SVG 판정(7.5). #21이 그대로 쓴다 |
| `publishing/render.py` | `share_render`, 1 | `share_model` | stdlib, 표시 | 공유 모델 → `{경로: 바이트}`. kit label은 영어, 사용자 글은 escape와 `lang=""` |
| `publishing/build.py`·`package.py`·`archive.py`·`extract.py` | `publishing`, 2 | `core`, `ports`, `read_model`, `share_model`, `publication_policy`, `share_render`, `publishing` | stdlib, 분석(Parquet 메타데이터), 표시 | 스냅샷 조립, loader, writer, manifest, 결박, ZIP, 형식별 추출기 |
| `__main__.py`(기존) | `kit_entry`, 3 | 지금 목록 + `publishing`, `publication_policy` | | `build` 하위 명령, 프로필 ID로 구현 조립, #19의 `ReportRenderer` 주입 |

- 투영과 공통 검사는 수준 1에 둔다. stdlib과 `read_model`, 그리고 import가 없는 자료형 계층 `share_model`만 가져온다. 그래서 같은 수준의
  형제끼리 import가 없고([아키텍처 §11](../ARCHITECTURE.md#계층과-의존-방향-검사)의 `layer-direction`), 계약 시험이 분석 라이브러리 없이 돈다.
- `share_render`는 `share_model`만 가져온다. renderer가 `core`·`read_model`·`ports`를 가져오는 변경은 `layer-direction`으로 실패한다.
- `publishing`은 `workspace`(`report.py`)와 같은 수준의 형제라 서로 import하지 않는다. `esc` 같은 작은 서식 함수는 renderer에 따로 둔다.
  #18이 Workspace를 "순수 자료 → HTML" 보기 함수로 나누면 그 함수를 더 낮은 계층에 두고 함께 쓰는 길을 #18과 맞춘다(R7).
- Parquet의 열 값·통계·key-value 메타데이터는 stdlib로 풀 수 없다(압축 페이지). 그 추출은 `publishing/extract.py`가 프로젝트 환경의 `pyarrow`로
  하고, 추출한 문자열을 `checks`의 대조기에 넘긴다. 새 프로젝트 템플릿은 `pyarrow`를 의존성으로 가진다. `pyarrow`를 가져오지 못하거나 파싱이
  실패하면 그 파일을 통과시키지 않는다(fail closed, 11.5).
- 모든 새 모듈은 `tests/test_layers.py`의 `LAYERS`·`MODULES`와 아키텍처 §11의 표에 PR-1·PR-2가 등록한다. SDK(`__init__.py`)는
  `publishing`을 가져오지 않는다(`import-time` 규칙).

## 4. `PublicationProjection` 계약

### 4.1 입력

- `ShareSnapshot`: `relations.read`의 결과로 만든 읽기 전용 보기. 기준(6.1), 기록 조회, 재생한 상태, 참조 판정, Report manifest와 파일 상태,
  그리고 지금 바이트의 해시를 묻는 `current_digest(identity)`만 제공한다. 프로젝트 경로를 열 수 있는 객체(`core.Project`)를 넘기지 않는다.
- **스냅샷 일관성.** relations의 `updating` 비교는 기록 파일의 stat만 보고(`relations.py:553-570`), Report content의 해시는 판정이 필요할 때
  계산한다(`relations.py:812-820`). 그래서 스냅샷을 만들 때 참조 판정, Report content 판정, 그 판정에 쓴 digest 표(기록된 해시가 있는 대상의
  지금 sha256)를 한 번에 미리 계산해 둔다. `current_digest`는 그 표만 보고 파일을 다시 열지 않는다. 계산은 relations와 같은 주입된 `sha`를
  쓴다. 같은 표에 파일 후보(그림·첨부 표·코드 스냅샷·Report 파일)의 형식 판정(처음 바이트, 11.5)도 둔다. 투영은 이것으로 역할이 허용하지 않는
  형식을 `not_included`(`format`)로 정한다. 투영이 파일 바이트에 닿는 길은 이 표뿐이다. 읽기 반환 뒤의 변경은 판정에 섞이지 않고, 복사 중의
  변경은 writer가 이 표와 대조해 `source_changed`로 잡는다(6.2). 7단계의 검사는 이 표를 믿지 않고 패키지 바이트로 형식을 다시 판정한다.
- `ShareRequest`: 범위(`project`·`reports`·`collection`과 고른 버전), 항목 제외 목록(OQ5), 선언한 분석 상태(OQ7), 철회 버전 허용(OQ10).
  UI locale은 `en` 고정이다(용어집 §7 "게시 locale").

### 4.2 출력

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Text:
    """Every string a page shows."""
    value: str
    origin: Literal["kit", "user"]  # user: escaped, lang=""; kit: an English label
    source: tuple[ItemKey, str] | None = None  # user text: the included item and its allowed field; never written out


@dataclass(frozen=True)
class FileSlot:
    """A file the renderer knows: where it is in the package and what was checked."""
    package_path: str
    role: Literal["figure", "attached_data", "code_snapshot", "report_file", "library"]
    verified: Literal["recorded_hash", "no_recorded_hash", "kit_generated"]


@dataclass(frozen=True)
class Reference:
    relation: str
    binding: str
    basis: str
    state: str              # ADR-0006 section 4, plus not_included
    reason: str | None
    category: str           # event, run, visualization, dataset, code, report, context, metric
    target: ItemKey | None  # only when the target is in the package


@dataclass(frozen=True)
class Projection:
    model: ShareModel     # no project path, no record dicts
    copy_plan: CopyPlan   # package path -> (project-relative source, recorded sha256 or None, role)
    ledger: ScopeLedger   # included ItemKeys; not_included counts by category and reason


class PublicationProjection(Protocol):
    id: str             # a registered implementation: "read", "evidence"
    rules_version: int  # raised when the allow-list changes; written to the manifest

    def project(self, snapshot: ShareSnapshot, request: ShareRequest) -> Projection: ...
```

`ShareModel`·`CopyPlan`·`ScopeLedger`·`ItemKey`는 내부 코드 이름이다. 용어집 등록 대상은 공개 이름(명령 인자·파일·manifest 필드·화면 글)이다.
`ScopeLedger`의 범위 밖 정체성 목록은 누출 검사(11.4)만 쓰고 renderer에 넘기지 않는다.

- **원장을 믿지 않는다.** build는 4단계 끝에서 `ledger.included ⊆ scope_closure(snapshot, request, profile_table)`인지 확인한다(5.2).
  넘으면 아무것도 쓰기 전에 `leak_found`(원인 `projection_out_of_scope`, 넘은 항목의 범주별 수)로 멈춘다. 금지 목록도 원장만으로 만들지
  않는다(11.4).
- **`Text.source`.** 사용자 글마다 그 글이 온 포함 항목과 필드를 담는 내부 필드다. 패키지에 쓰지 않는다. 누출 검사의 원본 짚기(11.6)와
  C1·C12 시험이 쓴다. renderer는 `{경로: 바이트}`만 돌려주므로 생성한 HTML의 바이트 위치에서 원래 기록으로 되짚을 길이 없다. 그래서 출처를
  짚는 검사는 모델 단계에서 먼저 한다.

### 4.3 모든 구현이 지킬 계약(계약 시험의 사례)

| # | 계약 | 시험 |
|---|---|---|
| C1 | **출처.** 모델의 모든 사용자 글은 포함한 기록의 허용 필드에서, kit 글은 영어 label 표에서 온다. 그 밖의 문자열은 없다 | 모델을 끝까지 걸어 사용자 글의 `Text.source`가 포함 집합(원장 ∩ 공통 닫힘)의 항목과 프로필 표가 그 종류에 허용한 필드인지, kit 글이 label 표에 있고 `source`가 없는지 대조 |
| C2 | **판정을 바꾸지 않는다.** 포함한 대상끼리의 참조는 상태·확정 여부·버전이 원본 판정과 같다. 바뀌는 것은 범위 밖 대상을 가리킨 참조의 `not_included`뿐이다 | 원본 `ReadModel`의 판정과 비교 |
| C3 | **구별.** 원본에서 `missing`인 대상은 범위와 무관하게 `missing`이고, 범위에서 뺀 대상만 `not_included`다. `content_changed`·`version_unbound`·`ambiguous`·`unknown`·`unsupported`를 합치지 않는다. 범위 밖 대상의 표시는 C12를 따른다 | 같은 대상을 제외·원본 삭제·해시 없음·버전 없음으로 만든 네 프로젝트 |
| C4 | **포함 관계.** 같은 요청에서 `evidence`의 출력은 `read`의 출력을 포함한다(PUBLISH §5 "read에 더해") | 두 실제 구현의 집합 비교 |
| C5 | **진행 중인 것 없음.** 완료 이벤트가 없거나 `running`인 run, `prepared`·`incomplete` Report는 내용 후보가 아니다(PUBLISH §3) | `running`·`no_completion_event`·prepared fixture |
| C6 | **과거 근거.** 결박된 참조는 그 버전만 가리킨다. 미결박 참조는 결박하지 않고 포함 대상을 늘리지 않는다. Report의 미결박 후보는 그 `based_on.as_of_seq`까지다(ADR-0006 §3) | Report v1이 A를 인용한 뒤 같은 키의 B가 생기는 fixture, `same_chart_id` |
| C7 | **결정성.** 같은 스냅샷과 요청이면 같은 `Projection`이다. 목록은 seq, `run_id`, 코드 포인트 순 경로 같은 명시적 키로 정렬한다 | 두 번 투영해 직렬화 바이트 비교 |
| C8 | **입출력 없음.** 투영은 파일을 열지 않는다 | 하위 프로세스에서 `sys.addaudithook`의 `open` 감사 이벤트로 무장하고(`builtins.open`·`io.open`·`os.open`·`Path.open`이 모두 잡힌다), digest 표를 미리 채운 스냅샷으로 투영 |
| C9 | **조용한 대체 없음.** 모르는 프로필, 모르는 Report 버전, 허용 없는 철회 버전, 완료되지 않은 버전은 사유와 함께 거절한다. 다른 범위·프로필로 바꿔 계속하지 않는다(베타 기준 Q15) | 실패 주입 |
| C10 | **복사 계획의 역할 제한.** 복사 계획에는 5.3이 허용한 역할의 파일만 있다. 기록 파일(`run.json`·`*.meta.json`·`journal.jsonl`·manifest)과 로그는 어떤 프로필에도 없다 | 복사 계획의 역할·이름 검사 |
| C11 | **조립.** 진입점이 주입한 구현만 쓰인다 | 등록된 실제 구현을 호출되면 실패하도록 무장하고 시험용 구현 `recording`을 주입해 build가 끝나는지, manifest와 이벤트의 프로필 ID가 그것인지(`tests/test_contracts.py`의 방식). 그리고 그 build의 manifest·이벤트가 프로필과 모델에서 온 값 말고는 기본 구현으로 만든 build와 같은 의미(ID 형식, 기준, 참조, 상태)인지 비교한다(ADR-0001 확인 2) |
| C12 | **범위 밖 대상은 이름 없음.** 포함 집합 밖의 대상은 상태(`not_included`·`missing`·`content_changed`·`unknown`·`unsupported` 등)와 관계없이 종류·상태·사유만 보인다. 이름·ID·경로·tooltip을 모델에 담지 않는다 | 모델의 어떤 `Text.source`도 포함 집합 밖 항목을 가리키지 않는지 확인. 포함한 발견이 범위 밖 데이터셋(기록은 있고 파일만 없는 `file_absent`, 8자 미만 이름 포함)·`content_changed` 코드·`unsupported` 참조를 가리키는 사례 |
| C13 | **닫힘 안.** 원장의 포함 집합은 공통 닫힘(5.2)의 부분집합이다. 구현은 더 적게 담을 수 있지만 넓힐 수 없다 | 실제 구현마다 범위 넷에서 비교. 넓히는 구현을 build가 막는지는 12.1의 적대적 투영이 본다 |

**필드 포괄 시험.** 기록 schema와 `/1` 기록의 필드 목록(현재 계약 C1–C4)의 모든 필드가 5.3 표의 포함이나 명시적 제외에 있어야 한다.
#13·#14·#19가 필드를 더하면 이 시험이 실패해 결정을 요구한다. 결정하지 않은 필드가 조용히 공유되는 일은 없다(기본은 제외).

### 4.4 구현과 조립

- `read`와 `evidence`는 공통 닫힘 함수(`publishing/scope.py`의 `scope_closure`, 5.2)를 쓰고 허용 목록 표(5.3)만 다르다. 표가 자료라서
  조직별 프로필(P2)도 새 표로 더한다. 거대한 `mode` 분기를 만들지 않는다(아키텍처 §5). 등록표는 구현 ID마다 프로필 표를 함께 둔다(시험용
  `recording`은 `read` 표). build는 같은 함수로 원장을 다시 확인하므로 어떤 구현도 닫힘을 넓힐 수 없다.
- 진입점이 `--profile` 값을 등록된 구현 ID 표에서 골라 넘긴다. 환경 변수나 기록 속 문자열로 고르지 않는다. 모르는 ID는 `profile_unknown`이다.
- 시험용 구현 `recording`은 받은 요청을 기록하고, 범위 페이지와 sentinel 하나만 담은 정해진 모델을 돌려준다. renderer가 투영 밖의 기록으로
  페이지를 채우는 결함이 있으면 이 구현의 패키지에 범위 밖 canary가 나타난다(12.2).

## 5. 범위 적용

### 5.1 시작점

| 범위 | 시작점 |
|---|---|
| `project` | 기준 CONTEXT 버전, 기준 시점의 분석 초점·지표 정의(#14), OQ3이 정한 종류의 기록(기준 시점의 상태 포함), OQ4가 정한 Visualization, 고른 Report 버전 |
| `reports` | 고른 `report_id + revision_id`들. 그 밖의 프로젝트 내용(목표, 다른 발견)을 첫 화면에 자동으로 더하지 않는다(PUBLISH §7). CONTEXT는 Report가 인용할 때만 |
| `collection` | 묶음 버전이 고정한 구성원 Report 버전(#19가 `analytics.report_collection/1` 등으로 정한 뒤). 부분 생성 상태는 기록대로 |

### 5.2 닫힘 규칙

포함 집합 = 시작점 ∪ 시작점의 직접 참조 대상 ∪ 그 대상의 부속. 그 이상 따라가지 않는다.

- 이 닫힘은 Strategy 밖의 공통 함수 `scope_closure(snapshot, request, profile_table)`(`publishing/scope.py`)다. 요청의 범위·항목 제외와
  프로필 표(어떤 부속을 따라가는지)를 반영한다. Strategy는 이보다 적게 담을 수 있지만 넓힐 수 없다(C13).
- build는 투영 뒤 원장의 포함 집합이 이 닫힘 안인지 확인하고, 넘으면 쓰기 전에 `leak_found`(원인 `projection_out_of_scope`)로 멈춘다.
  금지 목록(11.4)의 대상은 "스냅샷 − (원장 ∩ 공통 닫힘)"이다. 그래서 투영 결함이 제외한 run을 원장에 "포함"으로 적어도 그 run의 토큰은
  금지 목록에 남고, 바이트 단계가 그 run의 내용을 잡는다.

| 출발 | 따라가는 것 | 멈추는 곳 |
|---|---|---|
| Report 버전 | `based_on.refs`의 결박된 대상(정확한 버전)과 그 부속(OQ11) | 미결박 참조는 `version_unbound`로 표시만. 대상이 다시 가리키는 기록은 `not_included`(`out_of_scope`) |
| 발견·결정·질문 등 | `refs`의 `author`·`inferred` 결박 대상. `inferred`는 "Inferred link"로 보이고 확정으로 세지 않는다(ADR-0006 §3) | `none` 결박 |
| Visualization 버전 | 그림 파일(그림 역할이 허용한 형식만, 11.5), 그 run의 머리(요약). `evidence`면 첨부 표 | 그 run의 다른 그림 |
| run(`evidence`) | 코드 스냅샷, 입력·출력의 메타데이터, 재실행 비교 | 입력의 생산자 run과 계보. 포함되지 않은 생산자는 `not_included` |
| 데이터 관측·가져오기 | 데이터셋 메타데이터(`evidence`) | 데이터 바이트. 원시 데이터는 기본 제외(PUBLISH §5) |

- 항목 제외(OQ5)는 닫힘 뒤에 적용한다. 제외한 항목을 가리키는 참조는 `not_included`(`excluded`)가 되고, 제외한 항목을 거쳐서만 들어온
  대상도 빠진다.
- 계보를 따라가지 않는 이유: 보고서 → 차트 → run → 입력 → 생산자 run …을 따르면 프로젝트 전체가 딸려 온다. 필요한 대상은 사람이 범위에 넣는다.
- 진행 중이거나 기록이 끝나지 않은 run은 내용 후보가 아니다(C5). 첫 화면과 범위 구역에 "N runs were in progress or incomplete at this point and
  are not included"처럼 개수만 적는다.

### 5.3 허용 목록(필드 × 프로필)

포함은 "포함", 제외는 "—"다. 표에 없는 필드는 두 프로필 모두 제외다. "(OQn)"은 소유자 질문의 권장안을 따른 칸이다.

| 대상 | 필드 | read | evidence | 비고 |
|---|---|---|---|---|
| 프로젝트 | 표식의 `title` | 포함 | 포함 | 없으면 "Untitled project". 지금처럼 폴더 이름으로 대신하지 않는다(`core.py:203-204`) |
| | 루트 경로, 폴더 이름, `project_id` | — | — | `project_id`는 프로젝트의 `publication` 이벤트에만 |
| CONTEXT | 기준 버전의 본문, 버전 이름과 기록 시각 | 포함 | 포함 | 작업 중인 `CONTEXT.md`가 아니다(6.3) |
| | 다른 버전과 버전 diff | — | 포함(OQ2) | |
| 기록(OQ3이 정한 종류) | `id`, 종류 label, `text`, `recorded_at`(offset 그대로), 작성 주체의 역할, 기준 시점까지의 상태와 상태 이력 | 포함 | 포함 | 정정·철회된 기록도 상태와 함께(과거 판단 보존) |
| | `session`, 원래 `refs` 객체 | — | — | 참조는 `Reference`로만 |
| 메모(`note`) | 위와 같은 필드 | — | 포함(OQ3) | |
| 기록 종류 | `publication`, `publish_receipt`, #21의 게시 시작 기록 | — | — | 전달 기록이며 receipt는 대상 경로를 담는다 |
| | Workflow feedback, Record coverage, Tool usage, 재사용 후보·알림·모드, checkpoint | — | — | 내부 작업 기록(OQ3) |
| run | `run_id`, 제목, 실행·기록 상태와 사유, 시작·끝 시각, 작성 주체의 역할 | 포함 | 포함 | read에서는 포함한 Visualization·기록의 출처로만 |
| | `error.type`, `error.line` | 포함 | 포함 | (OQ2) |
| | `error.message`·`error.summary`, hint, `warnings` 글, `finalize_errors` 글 | — | — | 개수와 "not included"만(OQ2). 절대 경로가 자주 든다 |
| | 스크립트 경로(프로젝트 상대), 코드 스냅샷(스크립트·모듈) | — | 포함, 복사 | 기록 해시와 같을 때만 복사 |
| | 입력·출력: 프로젝트 상대 경로, 역할, `sha256`, 크기, 출력의 행 수·schema | — | 포함 | 프로젝트 밖은 "Outside the project"와 `sha256`만 |
| | build 때의 관측(입력 same·changed·missing, 출력 덮어씀, 코드 변경) | — | 포함 | "Checked when this copy was built"와 함께(6.5) |
| | 재실행 비교 | — | 포함 | |
| | stdout·stderr | — | — | (OQ2) |
| | `env.python`, `env.packages`, `env.kit_version` | — | 포함(OQ2) | |
| | `env.executable`, `env.platform`, `env.uv_lock_sha256`, `env.kit_sha256`, pid, `session` | — | — | `env.executable`은 프로젝트 절대 경로를 담는다 |
| Visualization 버전 | `visualization_id`, 제목, caption, 형식, 그림 파일 | 포함, 복사 | 포함, 복사 | html·svg와 원격 자원 그림은 7.5(OQ8). 그림 역할이 허용하지 않는 형식(CSV·PDF·XLSX 등, magic byte로 판정)은 `not_included`(`format`, 11.5) |
| | 첨부 표 파일, 행·열 수 | — | 포함, 복사 | HTML 미리보기 행은 없다(OQ2) |
| | 스크립트 줄 번호 | — | 포함 | |
| 데이터셋(가져오기) | 포함한 기록·run이 인용한 데이터셋의 이름 | 포함 | 포함 | |
| | 행·열 수, 열 이름과 형식, `sha256`, 크기, `description` | — | 포함 | |
| | `source.path`, `source.modified_at`, 데이터 바이트, 프로필 통계, 미리보기 | — | — | (OQ2) |
| Report 버전 | 제목, `created_at`, 작성 주체의 역할, `entry`, 기준 시점의 상태, Version n, `based_on`(참조와 기준) | 포함 | 포함 | |
| | `content/`의 파일 | 포함, 복사 | 포함, 복사 | 버전 단위로만(OQ9). 검사할 수 없는 형식(PDF·Office 등)이 있으면 OQ23 |
| | `request_id`, `request_fingerprint` | — | — | |
| 지표·초점(#14) | 기준 시점의 정의·초점, 인용된 정의 버전 | 포함 | 포함 | 필드는 #14 뒤 이 표에 더한다 |
| 보조 로그 | telemetry, queries | — | — | |
| 읽기 토큰 | `source_revision` | — | — | 기준이 아니다(ADR-0006 §5) |

### 5.4 참조와 없음의 표시

용어집 §6의 다섯 없음(Unknown·Not recorded·Missing·Not included·Unsupported)에 독자 쪽 사본의 한 가지를 더해 여섯을 글로 구별한다.
색으로만 구별하지 않는다. 영어 label은 후보이며 용어집 §6에 먼저 등록한다.

**포함 집합 밖의 대상은 상태와 관계없이 이름이 없다(C12).** `not_included`뿐 아니라 원본에서 `missing`·`content_changed`·`unknown`·
`unsupported`인 범위 밖 대상도 종류·상태·사유만 보인다. 이름·ID·경로·`title`·`alt`·`aria-label`·`data-*`를 모델에 담지 않는다. 예를 들어
포함한 발견이 가리키는 범위 밖 데이터셋의 파일이 없으면 "Missing in the project when this copy was built (file_absent) · dataset"이다. 8자 미만
데이터셋 이름은 약한 토큰이라 본문에서 찾지 않으므로(11.4) 검사가 아니라 이 규칙이 막는다. 강한 토큰이면 kit 스스로 `leak_found`를 낸다.

| 상태 | 언제 | 페이지 글(후보) | 링크 |
|---|---|---|---|
| `not_included` | 원본에는 있으나 이 게시본에서 뺐다. 사유: `profile`·`excluded`·`in_progress`·`remote_resources`·`scripts`·`format`·`out_of_scope` | Not included in this publication · <종류>(+짧은 사유) | 없다. ID·이름·tooltip이 없다(OQ6) |
| `missing` | build 때 원본에 기록이나 파일이 없었다(`record_absent`·`file_absent`·`digest_mismatch`) | Missing in the project when this copy was built (<사유>) | 없다 |
| Not recorded | 기록된 적이 없다(코드 스냅샷 기록이 없는 run 등) | Not recorded | 없다 |
| `unknown` | 기록된 해시가 없다(`no_digest`), 이름을 풀지 못했다 | No recorded hash / Unknown | 대상이 포함됐을 때만 |
| `unsupported` | 이 kit이 모르는 가족·버전·type이다 | Unsupported | 없다 |
| 이 사본에 없음 | manifest에는 있는데 독자의 브라우저가 불러오지 못했다 | Missing from this copy | 독자 쪽 스크립트가 판정(7.6) |

그 밖의 판정:

- `resolved`: 대상이 포함됐으면 패키지 안 위치로 잇는다.
- `content_changed`: "Changed in the project when this copy was built — the recorded bytes are not in this publication". build 때의 관측이므로
  시점을 붙인다(PUBLISH §3). 지금의 바이트를 복사하지 않는다.
- `version_unbound`: "Version not fixed in the record". 후보 가운데 포함된 것만 "Candidate (recorded before this record)"로 잇고 나머지는 개수만 보인다.
  Workspace의 "latest, not the cited version"(`report.py:54`) 링크는 공유본에 없다(#20 완료 조건 4, PUBLISH §3).
- `ambiguous`: 포함된 후보만 잇고 나머지는 개수만 보인다.

### 5.5 관계 재생성과 생략 안내

- 관계(#18의 1-hop)는 포함 집합 안의 간선만 담는다. 범위 밖으로 가는 간선은 종류별 개수의 한 줄로 묶는다("3 related items not included: code
  snapshot ×1, data observation ×2"). 제외한 대상의 이름·ID는 `title`·`aria-label`·`data-*` 어디에도 쓰지 않는다.
- 관계는 HTML 텍스트 목록이다. #18이 그래프 보기를 만들면 같은 범위로 만든 payload 파일(7.4의 방식)로 덧붙인다.
- Workspace와 현재 계약 A1의 생략 안내는 정본 파일(`run.json`·journal)을 가리킨다. 패키지에는 그 파일이 없으므로 패키지의 생략 안내는 패키지 안
  위치(그 run 페이지의 전체 목록, 타임라인의 다음 쪽)이거나 "Not included in this publication"이다. 프로젝트 파일 경로를 안내하지 않는다.

## 6. 기록 읽기 — 과거 근거를 지금 데이터로 바꾸지 않는다

### 6.1 한 번 읽고 기준을 고정한다

- build는 `relations.read`(`relations.py:1287`)를 한 번 부른다. 읽는 동안 파일이 바뀌면 두 번까지 다시 읽고 그래도 바뀌면 `updating`이다.
  build는 `updating`이면 패키지와 결박을 쓰지 않고 `records_updating`으로 끝나며 다시 실행하라고 알린다. 1단계가 이미 남긴 CONTEXT 버전은
  그대로 남는다(그 자체로 정상 기록이다). 섞인 스냅샷을 고정본이라고 부르지 않는다.
- **`as_of_seq`는 ADR의 뜻 그대로다.** manifest와 이벤트의 `basis.as_of_seq`는 ADR-0006 §5의 "그 시점까지 포함한 journal seq"이고, 현재 계약
  E(읽기 기준)와 같이 이 읽기가 본 마지막 seq(파싱·복구한 줄의 최대, `relations.py:287-289`)다. reader와 게시본에서 같은 이름이 같은 값을 갖는다.
- **내용 기준 `content_seq`(OQ15).** 띠와 내용 비교는 새 필드 `content_seq`를 쓴다. `as_of_seq` 이하에서 게시 계열 kind가 아닌 마지막 줄의
  seq다. 게시 계열은 schema에 두는 닫힌 집합이다. 지금은 정상 `analytics.event/2`의 `publication`·`publish_receipt`이고, #21이 ADR-0009 결정 7대로
  게시 시작 기록의 kind를 정하면 그때 더한다. 모르는 kind의 줄, `unsupported` 줄, 복구한 줄은 건너뛰지 않는다(보수적: 내용 기준이 늦어질 뿐
  다른 내용을 같다고 하지 않는다). 게시 계열 이벤트는 어떤 프로필에도 담지 않고 상태 재생도 바꾸지 않으므로(ADR-0006 §6의 표에 없음), 그
  뒤에 게시 계열 이벤트만 있으면 패키지 내용은 같다. 내용 기준 후보가 없으면 `content_seq=0`이고 기록 시각은 null이다.
  seq를 얻지 못한 손상 줄에 seq나 시각을 추정하지 않는다. 자세한 기록 규칙은 ADR-0014 결정 2를 따른다.
- `as_of_seq`는 정체성 파일(`publication.json`, `about.html`)에만 있고 내용 해시(10.4)에 들지 않는다. 그래서 앞 build의 `publication` 이벤트가
  `as_of_seq`를 올려도 같은 기록으로 다시 만든 build의 내용 바이트는 같다. `content_seq`는 새 공개 이름이므로 용어집 §6에 먼저 등록하고
  ADR-0014 결정 2에 기록 형식 필드로 제안했다. `as_of_seq`의 뜻을 바꾸지 않는다(OQ15).
- 이 규칙 덕분에 기준까지 되돌려 재생하는 읽기를 relations에 더하지 않고, 과거 기준으로 build하는 내부 API도 두지 않는다. 기준은 언제나
  build 때의 끝이다. 공개 명령에도 `--as-of`가 없다(9.5).
- `context_version`은 seq가 `as_of_seq` 이하인 마지막 `context` 이벤트가 가리킨 버전이다. 없으면 null이다(ADR-0006 §5). 두 값 사이에는 게시
  계열 줄만 있으므로 `content_seq` 이하의 마지막 `context` 이벤트와 같다.
- 이후의 모든 판단은 이 한 번의 읽기로 만든 `ShareSnapshot`에서 한다.

### 6.2 대상별 과거 바이트

| 대상 | 기록된 해시 | 복사 조건 | 다르거나 없으면 |
|---|---|---|---|
| Report 파일 | 있다(`files[].sha256`, manifest 바이트는 완료 이벤트의 `digest`) | 모든 파일이 같다 | 그 버전의 build를 거절한다(`report_not_shareable`). 고른 Report가 깨진 채 공유되지 않는다 |
| 코드 스냅샷 | 있다(`script.sha256`·`modules[].sha256`) | 지금 바이트가 기록 해시와 같다 | 항목은 담고 그 파일은 `content_changed`·`missing`으로 보이며 복사하지 않는다 |
| CONTEXT 버전 | 파일 이름의 sha 접두사와 파일 자체 | 버전 파일이 있고 맞다 | 프로젝트 범위의 기준 문서라 build 실패. Report가 인용한 버전이면 `missing`으로 보인다 |
| `/1` Visualization 파일, 첨부 표 | 없다(현재 캡처는 기록하지 않음, ADR-0006 §3) | 그 run 디렉터리의 그 버전 파일을 복사하고 "No recorded hash"를 보인다(OQ14) | 파일이 없으면 `missing`(`file_absent`) |
| `/2` Visualization 파일 | #13이 더하면 있다 | 해시가 같다 | `content_changed` |
| 데이터 관측 | 있다 | 메타데이터만. 바이트는 복사하지 않는다 | 메타데이터의 상태만 |

- writer는 복사하면서 해시를 다시 계산하고 스냅샷의 digest 표(4.1)와 대조한다. 투영 때 같았던 파일이 복사 중 바뀌면 `source_changed`로
  build를 끝낸다. 기록 해시가 없는 `/1` 그림·첨부 표도 지금의 digest를 snapshot 표에 고정한다.
  [ADR-0014 결정 4](../decisions/0014-publication-record-contract.md#4-manifest의-필수-계약)대로 후속 writer는 어느 사본이든 복사 중 변경을 대조한다.
  기록 뒤부터 build 전까지 바뀌었는지는 기록 해시가 없는 사본에서 알 수 없으므로 "No recorded hash"를 유지한다(R6).
- 원본은 프로젝트 안의 일반 파일이어야 한다. 끝까지 푼 경로가 프로젝트 밖이거나 일반 파일이 아니면 복사하지 않고 `unsupported`(`outside_project`)로
  보인다. 보안 판정은 끝까지 푼 경로로 한다는 ADR-0006 §9의 규칙을 따른다. 심볼릭 링크로 임의의 로컬 파일이 패키지에 들어오는 길을 막는다.
- 지금 경로의 다른 바이트(다시 쓴 파생 표, 고친 스크립트)를 "그 버전"으로 복사하는 일은 어떤 경우에도 없다.

### 6.3 CONTEXT 기준

build는 작업 중인 `CONTEXT.md`를 그리지 않고 기준 버전 파일을 그린다(지금 Workspace는 `CONTEXT.md`를 그린다). OQ19의 권장안대로 build는
render·record·import처럼 읽기 전에 `core.sync_context`(`core.py:665`)로 바뀐 `CONTEXT.md`를 새 버전으로 남긴다. 그래야 "빌드 시점의 초점"
(PUBLISH §3)이 기준 버전의 바이트와 같다. `--preview`는 기록을 쓰지 않는다. 대신 build가 남길 버전을 메모리에서 만들어 그 내용으로 같은
투영과 검사를 하고(3절), "CONTEXT: pending — build will record a new version first"라고 알린다. 그래서 바뀐 `CONTEXT.md`에 로컬 경로가
있으면 미리보기도 build처럼 `leak_found`를 낸다. 기준 CONTEXT는 `--exclude`로 뺄 수 없으므로, 걸리면 `CONTEXT.md`를 고쳐 새 버전으로
남기는 것이 바로잡는 길이다(11.6).

### 6.4 두 기준의 표시

보고서 중심 게시본은 게시본의 기준(`content_seq`·`context_version`)과 Report의 `based_on`을 따로 보인다. build가 늦어도 Report의 분석 기준을 최신으로
바꾸지 않는다(PUBLISH §3). Report가 인용한 발견이 기준 전에 정정·철회됐으면 인용한 글은 그대로 두고 "Superseded at this snapshot"처럼 기준 시점의
상태를 함께 보인다(보고서 설계 §3).

### 6.5 build 때의 관측

입력 상태(same·changed·missing), 출력의 현재 상태, 코드 상태는 build가 프로젝트 파일을 본 관측이다. `evidence`에서만 보이고 "Checked when this copy
was built"를 붙인다. 게시본이 원본을 계속 보는 것처럼 쓰지 않는다(PUBLISH §3). 관측 시각은 정체성 쪽(`built_at`)에 둔다.

## 7. 패키지

### 7.1 배치

```text
<publication_id>/
  index.html                         entry. 스냅샷 띠, 첫 화면(7.3), 범위 구역(#scope)
  about.html                         About this copy: publication_id, 빌드 시각, renderer, 범위 요약, 한계, 알림. 정체성 파일
  README.txt                         폴더·ZIP을 받은 사람을 위한 영어 고정 안내
  publication.json                   manifest(10.3). 자기 자신은 목록에 없다
  pages/
    context.html                     기준 CONTEXT 버전(evidence면 버전 diff도)
    timeline/<n>.html                포함한 기록. seq 순서, anchor e<seq>. 큰 기록은 쪽으로 나눈다
    runs/<k>.html                    포함한 run. <k>는 kit이 만든 이름(7.2)
    data/<k>.html                    포함한 데이터셋의 메타데이터(evidence)
  reports/<k>/                       포함한 Report 버전 하나(report_id + revision_id)
    index.html                       Report 보기 페이지
    content/<manifest의 경로>         보존 바이트 그대로
  files/<sha256>.<ext>               그 밖의 복사 바이트: 그림, 첨부 표, 코드 스냅샷
  assets/
    publication.css  publication.js  code.css
    identity.js                      정체성 파일. about.html과 같은 값
    plotly.min.js                    plotly 그림이 있을 때만
    figures/<sha256>.js              plotly 그림 payload. 그림 하나에 한 파일
    NOTICE.txt                       포함한 라이브러리의 이름·버전·라이선스 고지(PUBLISH §4)
```

- 이름은 PUBLISH §4 예시와 일부 다르다. 그 예시는 확정 API가 아니다. kit이 정하는 이름은 기록 정체성에서 직접 만들지 않는다(7.2). 원래
  기록과의 대응(PUBLISH §4 "공유본의 ID는 원래 기록과 대응")은 페이지의 글과 manifest `included`(`ItemKey` ↔ 패키지 경로)가 맡는다.
- `files/`는 내용 주소다. 같은 바이트는 한 번만 담고, 사용자 파일 이름이 패키지 이름으로 새지 않으며, 대소문자·예약어·정규화 충돌이 생기지 않는다.
  확장자는 11.5가 판정한 형식에서 소문자로 정한다(`png`·`jpg`·`gif`·`webp`·`svg`·`html`·`json`·`parquet`·`py`·`txt`). `bin`은 없다. 판정하지
  못했거나 역할이 허용하지 않는 형식의 파일은 패키지에 넣지 않는다(11.5). `html`·`svg`는 7.5의 조건을 통과한 파일에만 쓴다. 원래 basename은
  글이나 `<a download="…">`의 값으로만 보인다.
- Report 파일은 `content/` 아래에 manifest 경로를 그대로 둔다. 본문의 상대 링크가 그대로 동작해야 하기 때문이다. 그 경로는 #19가 저장할 때
  ADR-0006 §7의 이식 규칙으로 검사한 새 이름이다.
- 원래 기록 파일(`run.json`·`*.meta.json`·`journal.jsonl`·Report manifest)은 패키지에 없다. 투영한 값만 있다(C10).

### 7.2 이름 규칙

- kit이 정하는 파일·디렉터리 이름(`pages/runs/`, `pages/data/`, `reports/`)은 기록 정체성(`run_id`, view 이름, `report_id`·`revision_id`)에서
  직접 만들지 않는다. `<k>`는 그 대상 `ItemKey`의 정규 문자열을 sha256으로 만든 앞 16자(소문자 16진수)다. 원래 이름은 페이지의 글(`lang=""`)과
  manifest `included`의 대응으로만 보인다.
- 이유: ADR-0003 결정 6은 새 이름만 거절하고, 기록에 남은 이름(지금 dataset 이름 검사를 통과하는 `con`·`nul` 등)은 계속 읽히고 다시 실행되게
  했다. 기록 이름은 바꿀 수 없으므로 그 이름으로 패키지 이름을 만들고 이식성 검사로 멈추면 그런 프로젝트는 build를 영영 할 수 없다.
  `_platform.portable_stem`은 `_`가 들 수 있는 이름에 단사가 아니므로(`_platform.py:1247-1257`) view 이름에 쓸 수 없고, `revision_id`
  (`opaque_id`)는 대소문자만 다른 두 값이 casefold로 겹친다.
- 16진수 이름에는 예약 이름·대소문자·정규화 충돌이 생기지 않는다. 두 대상의 `<k>`가 겹치거나(64비트 접두사의 충돌) kit이 만든 이름이 11.2
  검사에 걸리면 kit 결함이고 `package_check_failed`로 멈춘다. 이름을 몰래 바꾸지 않는다.
- 이름과 anchor는 포함한 대상에만 만든다. 제외한 대상의 이름이 들어갈 자리가 없다. 차트 anchor처럼 키의 해시를 쓰는 anchor도 포함한 대상에만
  있으므로 해시로 제외 대상의 이름을 추측해 확인하는 길이 닫힌다.
- Report의 `content/` 아래 경로는 사람이 고를 수 있는 내용이다. #19의 검사를 거친 경로가 그래도 11.2에 걸리면 그 Report 버전은
  `report_not_shareable`(사유 `path`)이고, 작성자가 이식 가능한 경로로 새 버전을 저장해 빠져나간다.
- 심볼릭 링크와 빈 디렉터리를 만들지 않는다.
- 패키지 상대 경로(최상위 폴더 포함)의 길이에 상한을 두고 넘으면 미리보기에서 경고한다. 값은 구현 PR이 Windows에서 측정해 정한다.

### 7.3 첫 화면과 스냅샷 띠

**스냅샷 띠.** 모든 페이지의 `<header>`에 같은 띠를 HTML로 넣는다. JS가 필요 없고 build마다 같은 바이트다(정체성 칸 제외).

| 줄 | 예(영어 UI) | 출처 |
|---|---|---|
| 성격 | Shared snapshot — a fixed copy that does not update | 고정 글 |
| 대상 | 프로젝트 제목(`lang=""`). 없으면 "Untitled project" | 표식의 `title` |
| 기록 시점 | Records up to 2026-10-07 15:37 +09:00 (e410) | `content_seq` 이벤트의 `recorded_at`(기록된 offset 그대로, 6.1) |
| 맥락 | Context version recorded 2026-10-07 15:30 +09:00. null이면 Context: not recorded at this point | `context_version`과 그 `context` 이벤트 |
| 분석 상태 | Analysis: in progress (declared when built) | 선언(OQ7) |
| 범위 | Scope: read profile · 37 items not included → `index.html#scope` | 투영의 원장 |
| 보고서 중심이면 | Report "…"(`lang=""`) · Version 2 · saved 2026-09-18 10:02 +09:00 · based on records up to e120 | Report manifest와 완료 순서 |
| 정체성 | Built … · Publication pub-… — `assets/identity.js`가 채운다. JS가 없으면 "About this copy" 링크(`about.html`) | 정체성 파일 |

- 띠와 페이지는 "latest"·"current version"·"verified"를 쓰지 않는다. 패키지는 자기가 최신인지 알 수 없다(ADR-0009 결정 4). 최신 주소와 이전
  버전 목록은 #21의 channel 페이지가 보인다. 그래서 되돌리기로 최신이 된 오래된 build에서도 패키지의 글은 그대로 참이다.
- 시각은 기록된 문자열을 offset과 함께 보인다. build 기기나 독자의 시간대로 바꾸지 않는다(용어집 §7. 지금 Workspace는 offset을 버린다).
- 인쇄 CSS는 띠를 인쇄한다.

**첫 화면.**

- 프로젝트 범위: 목적 → 현재 지표·질문 → 유효한 발견 → 포함된 근거 → 다음 확인(PUBLISH §7), 이어서 고른 Reports, Visualizations, 범위 구역.
- 보고서 범위: 고른 본문이나 묶음을 먼저 보이고 작성 시점·`based_on`·Version n·포함된 근거로 잇는다(PUBLISH §7).
- 범위 구역(`#scope`): 프로필과 `rules_version`, 범주별 포함 수, 범주·사유별 제외 수, 진행 중이라 빠진 run 수, 형식 때문에 빠진 파일 수,
  해시 없이 복사한 파일 수, 검사 상태별 파일 수(11.5), 경고(일반 경로 모양 OQ24, 이미지 메타데이터 OQ25), 한계,
  "Interactive figures include the data they plot (N points)", 없음의 여섯 표시 설명. 범위의 정본 표시는 이 구역이고 `about.html`은 같은 요약을
  정체성과 함께 다시 보인다.
- **재현 가능 주장 없음**(베타 기준 Q14, PUBLISH §5). kit label 표에 "reproducible"·"re-run"·"complete evidence" 같은 주장이 없다. read
  패키지의 범위 구역은 "Code and data are not included in this publication; it cannot be re-run from this copy"를 적고, evidence 패키지도 데이터
  바이트가 없으므로 "Input data is not included"를 적는다.

### 7.4 독자 쪽 동작

- 모든 자원은 페이지가 정적으로 참조한다(`<script src>`·`<link href>`·`<img src>`·`<iframe src>`). `fetch`·XHR·동적 `import()`를 쓰지 않는다.
- plotly 그림의 payload는 `assets/figures/<sha256>.js`(`window.AnalyticsFigures["<sha256>"] = {…};`)다. 페이지는 그림 자리에
  `data-payload="…/assets/figures/<sha256>.js"`를 두고, `publication.js`의 한 함수만 이 속성 값으로 script 요소를 만든다. 정적 링크 검사는
  `data-payload`를 자원 참조로 보고 패키지 안·manifest 안인지 확인한다. 같은 그림이 여러 페이지에 나와도 파일은 하나다(지금 Workspace는
  `index.html`에 같은 그림을 두 번 넣는다).
- payload는 한 직렬화기로 만든다: 키 정렬, `ensure_ascii=False`, `<`·`>`·`&`·U+2028·U+2029를 `\u` escape. plotly figure도 이 직렬화기를 거친
  사본이고 manifest의 `derived_from`이 원본 파일과 해시를 가리킨다.
- `publication.js`가 하는 일은 탭·필터의 점진적 개선, plotly 그리기, "Load figure (N MiB)" 게이트(7.7), 이 사본에 없는 파일의 표시(7.6)뿐이다.
- 출하 JS(`publication.js`, 머리의 inline script)에 `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `navigator.sendBeacon`, `import(`이
  없다. 정적 검사가 이 이름을 막는다.
- `localStorage`·`sessionStorage`를 쓰지 않는다(지금 `report.js:7-10`은 쓴다). `file://`에서는 여러 파일이 저장소를 함께 쓸 수 있어 한 게시본이
  다른 게시본이나 Workspace의 값을 읽거나 남길 수 있다. 복귀는 URL anchor와 뒤로 가기로 한다(#18).
- live reload(`assets/build.js` 폴링)와 `?v=<build>`가 없다. 버전 디렉터리는 바뀌지 않으므로 캐시를 깰 필요가 없고, 붙이면 결정성이 깨진다.
- 모든 페이지에 `<meta name="referrer" content="no-referrer">`와 `<meta name="robots" content="noindex">`를 둔다. 원격 이동 링크에는
  `rel="noopener noreferrer"`를 단다. 버전 주소와 사내 경로가 Referer로 나가지 않고, 사내 HTTP로 내보낸 폴더가 색인되지 않는다.
- CSP meta(`connect-src 'none'`, 자원을 패키지 안으로 묶는 지시)는 브라우저 측정 뒤에만 채택한다. `file://`에서 `'self'`의 해석이 엔진마다 다르기
  때문이다. PR-5가 Chromium·Firefox·WebKit × `file://`·HTTP 하위 경로에서 재고 채택했다(2026-10-27 소유자 결정, "PR5 구현 구체화").

### 7.5 그림과 사용자 HTML·SVG(OQ8)

| 형식 | 패키지에서 | 독자 쪽 |
|---|---|---|
| png·jpg·gif·webp | `files/<sha256>.<ext>`로 복사, `<img alt>`(제목, `lang=""`) | 못 불러오면 "Missing from this copy" |
| svg | 아래 조건을 통과할 때만 복사하고 `<img>`로만 보인다. SVG 파일로 가는 이동 링크를 만들지 않는다 | 통과하지 못하면 `not_included`(`scripts` 또는 `remote_resources`) |
| plotly | 7.4의 payload. 원격 자원이 필요한 trace·layout(`scattergeo`, `choropleth`, `scattermapbox`, `choroplethmapbox`, `densitymapbox`, `scattermap`, `choroplethmap`, `densitymap`, layout `geo`·`mapbox`·`map`, 원격 `layout.images[].source`)이 있으면 담지 않는다 | `not_included`(`remote_resources`)와 제목·caption, `evidence`면 첨부 표 링크. 목록은 구현 PR이 plotly 판마다 측정해 고정한다 |
| html(altair·`_repr_html_`) | 아래 조건을 통과할 때만 `files/<sha256>.html`로 복사하고 `allow-scripts` 없는 `<iframe sandbox="allow-same-origin">`으로 연다(2026-10-27 소유자 결정: `file://`에서 자기 파일을 불러오게) | 통과하지 못하면 `not_included`. altair 기본 HTML은 vega를 CDN에서 부르므로 대개 여기에 든다(측정 안 함) |

- **조건.** 스크립트(`<script>`, `on*` 이벤트 속성, `javascript:` URL), 원격 자원 참조(ADR-0009 결정 9의 자원 참조 가운데 scheme이 있는 것,
  CSS `url()`·`@import` 포함), `<base>`, meta refresh, 중첩 문서(`iframe`·`object`·`embed`), SVG `foreignObject`·외부 `href`가 하나도 없어야 한다.
  판정은 `publishing/checks.py` 하나가 하고 build와 `--verify`가 같은 함수를 쓴다.
- **Report의 HTML·SVG 파일**이 조건을 통과하지 못하면 그 Report 버전은 버전 단위로만 넣으므로(OQ9) build가 `report_not_shareable`(사유
  `scripts`·`remote_resources`, 파일과 위치)로 멈춘다. 작성자는 스크립트 없는 새 Report 버전을 저장해 그 버전을 고른다.
- 정적 이미지 대체(그림 변환)는 P0 밖이다. 변환 의존성이 없다.
- **Report 보기 페이지.** 보기 페이지(`reports/<k>/index.html`)와 본문 파일(`reports/<k>/content/…`)의 위치가 다르고 `<base>`는 금지다(11.1).
  그래서 #19의 `ReportRenderer`를 공유에 주입할 때의 계약은 이렇다: 바이트와 `{manifest 경로: 패키지 경로}` 대응만 받고 파일 시스템을 보지
  않는다. entry 본문(Markdown 등)의 상대 URL은 entry 파일의 디렉터리를 기준으로 풀어 보기 페이지 기준의 패키지 상대 경로로 다시 쓴다. 대응에
  없는 상대 경로는 링크 규칙 위반으로 다룬다(Report 본문이면 `report_not_shareable`). HTML entry는 iframe으로 열므로 본문 안의 상대 경로가
  그대로 동작한다.
- plotly.js는 지금처럼 프로젝트 환경의 `plotly` 패키지에서 복사한다. 포함한 plotly 그림이 있는데 얻을 수 없으면 `renderer_unavailable`로 실패한다.
  빈 그림을 조용히 내보내지 않는다. 프로젝트 환경은 사용자가 바꿀 수 있으므로 번들 사본도 원시 바이트를 검사한다(11.5).
- plotly 그림은 그린 데이터 전체(hover·customdata 포함)를 담는다. 미리보기 출력, 그림 옆, 범위 구역과 `about.html`에 "Interactive figures include the
  data they plot (N points)"를 보인다. N은 그 그림 trace들의 가장 긴 데이터 배열 길이의 합이고 정의는 구현 PR이 고정한다.

### 7.6 이 사본에 없는 파일

정적 페이지가 `file://`에서 할 수 있는 만큼만 하고 나머지는 한계로 적는다. 독자 기기의 전체 대조는 #48이다.

- 페이지 머리의 작은 inline script가 `window`에 capture 단계의 `error` 처리기를 단다. `img`·`script`·`link`의 불러오기 실패를 그 자리의
  "Missing from this copy: <패키지 경로>"로 바꾼다. 처리기보다 먼저 실패한 이미지는 `DOMContentLoaded` 뒤 `complete && naturalWidth === 0`으로
  다시 찾는다.
- `iframe`(사용자 HTML 그림, HTML Report 본문)은 이 처리기의 대상이 아니다. 브라우저는 iframe 문서를 불러오지 못해도 `error`가 아니라 `load`를
  내고(MDN iframe의 error·load 이벤트 동작), 부모의 스크립트는 frame 문서를 판정하지 않는다. 그래서 iframe 옆에 늘
  보이는 글 "If this frame is empty, its file is missing from this copy: <패키지 경로>"를 둔다. iframe 문서가 빠진 것을 페이지가 스스로 판정하지
  못한다는 것은 범위 구역과 현재 계약 G에 한계로 적는다.
- CSS가 없을 때만 보이는 글을 모든 페이지 머리에 둔다: "This page could not load its files. If you opened it from inside a ZIP archive, extract
  the whole archive first; otherwise this copy is incomplete." `publication.css`가 이 글을 숨긴다. JS 없이도 동작한다. JS가 켜져 있는데
  `publication.js`가 없으면 inline script가 이 글을 보이게 한다.
- 패키지 루트의 `README.txt`가 같은 안내(폴더 전체를 옮길 것, ZIP은 전부 풀 것, `index.html`을 열 것, 이 사본은 고정 스냅샷이라는 것)를 영어로 적는다.
- 다른 HTML 페이지가 없는지는 정적 페이지가 미리 알 수 없다. 링크를 누르면 브라우저의 "file not found"가 나온다. 이것은 범위 구역과 현재 계약 G에
  한계로 적는다.

### 7.7 JS 꺼짐·접근성·큰 기록

- 모든 구역과 관계 목록이 HTML에 있다. 탭은 점진적 개선이다. 숨김은 화면 정리일 뿐이고 숨긴 구역도 포함한 내용이다. CSS로 감추는 제외는 없다.
- plotly 자리에는 "Interactive figure — needs JavaScript", 제목·caption, `evidence`면 첨부 표 링크가 있다.
- **첨부 표의 범위**(PUBLISH §5 "첨부 표가 요약 또는 일부 계열이면 그 범위를 표시", 베타 기준 Q13). kit은 첨부 표가 그림의 전체 데이터인지 요약·
  일부 계열인지 모른다. 그래서 label은 "Data attached by the script (N rows × M columns)"이고 "data behind this figure"나 "full data"라고 쓰지
  않는다. 행·열 수는 기록이나 Parquet 메타데이터에서 온다. plotly 그림이면 그림의 점 수와 첨부 표의 행 수를 나란히 보인다. 요약·일부 여부를
  기록하는 필드가 #13·#14에서 생기면 5.3에 더하고 그 범위를 글로 보인다.
- UI 글은 영어이고 문서 `lang="en"`, 사용자 글을 담은 요소는 `lang=""`다(#10, 현재 계약의 #10 절). 사용자 글은 escape만 하고 번역·정규화하지 않는다.
  CONTEXT와 Markdown Report는 HTML을 끈 Markdown으로 그린다. manifest에 `ui_locale: "en"`을 적는다.
- landmark(`header`·`nav`·`main`·`footer`), "Skip to content", 표 머리, 초점 표시. 상태는 색이 아니라 글로 적는다. plotly 자리는 `role="img"`와
  `aria-label`(제목). 사용자 글 요소의 `dir="auto"`는 E2E로 확인한 뒤 더한다.
- **M 규모 독자 대책.** 3,000,000바이트(지금 `PLOTLY_EMBED_LIMIT`, `report.py:30`)를 넘는 plotly 그림은 자동으로 불러오지 않고 "Load figure
  (N MiB)" 버튼을 둔다. 타임라인은 정해진 개수의 쪽(`pages/timeline/<n>.html`)으로 나눈다. 쪽 크기와 게이트 값은 M fixture에서 재서 정한다.
  참조 12개 밖은 그 run 페이지의 전체 목록으로 잇는다.

## 8. 명령과 API

### 8.1 사람 명령(제안)

```text
analytics build [--profile read|evidence]
                [--scope project|reports]
                [--report <report_id>[@<revision_id>]]... [--collection <collection_id>@<version>]
                [--exclude <ref>]... [--analysis-state in_progress|complete]
                [--allow-withdrawn] [--zip <path>] [--preview]
analytics build --list
analytics build --verify <folder-or-zip>
analytics build --zip-of <publication_id> --zip <path>
```

- `--report`·`--collection`을 주면 범위는 `reports`이고 아니면 `project`다. `--scope project --report X`는 고른 Report 버전을 Reports 구역에 담은
  프로젝트 읽기 화면이다. `--collection`은 #19가 묶음 형식을 정한 뒤에 연다.
- `@<revision_id>`를 빼면 그 Report의 head를 고르고, 결과와 manifest에 고른 버전을 적는다(최신을 조용히 쓰지 않도록 출력에 버전을 보인다).
- `--exclude`의 `<ref>`는 화면과 `analytics_status`가 쓰는 식별자 그대로다: `e<seq>`, `run_id`, `<run_id>/<visualization_id>`, 논리 키
  `script::id`, 데이터셋 이름, `<report_id>@<revision_id>`(OQ5).
- `--preview`는 3절대로 기록·파일을 쓰지 않고 메모리에서 끝까지 해 본다. 보이는 것: 기준(`as_of_seq`·`content_seq`, CONTEXT 상태 `recorded`·
  `pending`), 범주별 포함 수와 대략의 크기, 범주·사유별 제외 수, 진행 중이라 빠지는 것, 원격 자원·스크립트·형식 때문에 빠지는 파일, 해시 없이
  복사할 파일 수, 검사 상태별 파일 수, "Interactive figures include the data they plot (N points)", 경고(일반 경로 모양, 이미지의 GPS·작성자 키,
  포함 기록 제외 필드의 긴 줄), 검사 결과와 위치. build 출력도 같은 `basis`를 적으므로 둘의 기준이 다르면 사람이 알 수 있다.
- `--list`는 이 프로젝트의 build를 결박 seq 순서로 보인다: `publication_id`, 결박 seq, 프로필, 범위, `as_of_seq`·`content_seq`,
  `content_sha256`. 상태는 평행 어휘를 만들지 않고 둘로 나눈다. 기록 쪽은 ADR-0006 §6의 `recording_status`다: `complete`(결박 이벤트 있음),
  `incomplete`와 사유(`not_final`: manifest가 없음, `no_completion_event`: manifest는 있으나 결박 이벤트가 없음). 관측한 폴더 쪽은 현재 계약의
  파일 비교와 같은 `same`·`changed`·`missing`이다(`verify_package`로 manifest·결박과 대조). 같은 `content_sha256`의 이전 build가 있으면
  "same content as pub-…"을 적는다.
- `--verify`는 폴더나 ZIP을 그 manifest와 대조한다(11.3). 이 프로젝트 안에서 부르면 결박과도 대조한다. #21의 확인과 #48이 같은 함수를 쓴다.
- `--zip-of`는 결박된 폴더를 결박과 대조한 뒤 ZIP만 다시 만든다(ZIP 단계가 실패했을 때).
- build는 사적이고 공유는 publish(#21)나 ZIP 전달이다. 결과에 "Open <path> to check it before you share it"을 적는다.

### 8.2 출력과 오류

출력은 다른 kit 명령처럼 JSON 한 줄이다: `ok`, `publication_id`, 프로젝트 상대 폴더 경로, `package_sha256`, `content_sha256`, `files`, `bytes`,
`profile`, `basis`(`as_of_seq`·`content_seq`·`context_version`), 포함 요약, `not_included`(범주·사유별 개수), 검사 상태별 파일 수, `limits`,
`warnings`, ZIP 경로. 실패는 `{"ok": false, "error": "<code>", …}`다.

| 오류 코드(제안) | 언제 |
|---|---|
| `profile_unknown` | 등록되지 않은 프로필 ID |
| `scope_invalid` | 범위 조합 오류, 없는 Report, 형식이 틀린 `--exclude` |
| `report_not_shareable` | prepared·incomplete 버전, 파일이 manifest와 다름, 허용 없는 철회 버전, 조건을 통과하지 못한 Report HTML·SVG, 검사할 수 없는 형식의 파일(사유 `format`, OQ23), 이식할 수 없는 content 경로(사유 `path`). 사유와 위치 |
| `records_updating` | 읽는 동안 기록이 계속 바뀜 |
| `source_changed` | 복사하는 동안 원본이 바뀜 |
| `leak_found` | 누출 검사(11.4–11.6)와 닫힘을 넘은 원장(원인 `projection_out_of_scope`). 위치와 원본, 바로잡는 길 |
| `inspection_failed` | 허용 형식인데 추출기가 없거나(`pyarrow`) 파싱이 실패함. 파일과 형식(fail closed, 11.5) |
| `package_check_failed` | 링크 규칙, 없는 참조, manifest 밖 파일, kit이 만든 이름의 이식성·충돌(kit 결함, 7.2) |
| `renderer_unavailable` | 필요한 표시 라이브러리·plotly.js가 없음 |
| `publication_not_recorded` | 결박 이벤트를 확인하지 못함. `binding`이 `unbound`(다시 읽어 없음)나 `unknown`(다시 읽지 못함). 폴더는 남긴다(8.4) |
| `zip_path_invalid` | ZIP 경로가 프로젝트 안이거나 이미 있음 |

#13 writer의 오류(`writer_too_old` 등)는 그대로 전한다. 새 이름은 모두 용어집에 먼저 등록한다.

### 8.3 호스트·모델·SDK

- `/analytics build`는 `/analytics render`와 같은 경로로 kit 하위 명령을 프로젝트 환경에서 부른다. 상시 모델 도구는 더하지 않는다(PUBLISH §1,
  ADR-0001 결정 8). 에이전트는 사용자가 명시적으로 요청할 때 같은 CLI를 쓴다. run 뒤 자동 render처럼 build를 자동으로 부르지 않는다.
- build는 분석 코드를 실행하지 않고 모델을 부르지 않는다(Q16, 보고서 설계 §7).
- P0에는 SDK 함수가 없다. run 안에서 build하면 결과 폴더가 run의 출력과 섞일 수 있다. 내부 API는 `publishing.build.build_publication(project,
  request, *, projection)`이고 시험이 쓴다.

### 8.4 중단

- 런처는 SIGTERM을 `run`에만 전하므로(ADR-0004 결정 7) build는 SIGTERM으로 멈추지 않는다. Ctrl+C(SIGINT)는 같은 process group의 kit에 닿는다.
- 중단을 받으면 다음 단계를 시작하지 않는다. 9단계(결박)를 시작하기 전이면 결박하지 않고 자기 폴더를 지우려 시도한다.
- **9단계를 시작한 뒤에는 어떤 경우에도 폴더를 지우지 않는다.** `append_jsonl`이 줄을 다 쓴 뒤에도 예외나 중단이 호출자에게 닿을 수 있다
  (`core.py:575-576`. 닫기·flush의 오류, 그 사이의 SIGINT). 그때 폴더를 지우면 결박 이벤트는 남았는데 패키지는 없어진다. 그래서 9단계의 예외·
  중단 뒤에는 journal 잠금 안에서 그 `publication_id`와 `package_sha256`을 담은 `publication` 이벤트를 다시 읽는다. 있으면 완료(결박됨)로,
  없으면 `unbound`로, 읽지 못하면 `unknown`으로 알리고 폴더를 둔다. 호출의 반환이 아니라 관찰한 상태로 판단하는 ADR-0009 결정 5의 "결과가
  불확실한 호출"과 같은 방식이다. 끊긴 줄은 reader가 `damaged`로 읽으므로 이벤트가 아니고 `unbound`다.
- 강제 종료로 다시 읽지 못했으면 폴더가 남고, `--list`가 journal에서 결박 여부를 다시 판정한다.
- 결박 뒤면 build는 완료이고 ZIP 실패만 알린다.

## 9. 출력·결박·ADR-0009

### 9.1 build 폴더(OQ18)

- 위치는 관리 디렉터리 아래 `.analytics/publications/<publication_id>/`다. 관리 디렉터리는 run의 출력 비교에서 빠진다(지금 `.lab`이
  `core.py:57`의 `IGNORED_DIRS`에 있어 `runner.py:112`의 `tree_stat`이 보지 않는 것과 같은 이유. `runner.py:699`의 `MANAGED_DIRS`는 스크립트를
  가져오지 않는 디렉터리다). 프로젝트의 보통 위치면 다음 run이 출력으로 기록한다.
- 지금 템플릿의 `.gitignore`는 `.lab/` 전체가 아니라 `cache/`·`lock`·`adhoc/`만 무시한다. `evidence` 패키지는 git이 무시하는 첨부 표를 담으므로
  새 프로젝트 템플릿과 #13의 전환이 `.analytics/publications/`를 무시해야 한다. 그 줄이 없는 기존 프로젝트에서는 build가 경고하고 사용자 파일을
  고치지 않는다(R5).
- 자동 삭제는 없다. 결박 없는 폴더는 `--list`가 보이고 사람이 지운다. 정리 명령은 후속이다.

### 9.2 단계

| 단계 | 하는 일 | 실패·중단이면 |
|---|---|---|
| 0 잠금 | `.analytics/publications/.lock`을 `FileLocks.exclusive`로 잡고 결박까지 유지 | 기다린다(9.5) |
| 1 CONTEXT | (OQ19) `core.sync_context` | #13 writer의 오류 |
| 2 읽기 | `relations.read` | `records_updating`(1단계의 CONTEXT 버전은 남음) |
| 3·4 스냅샷·투영 | 6.1, 4절. 투영 뒤 공통 닫힘 확인과 모델 단계 누출 검사 | 거절 사유 그대로, `leak_found`(아무것도 쓰기 전) |
| 5 폴더 | 새 `publication_id`로 폴더를 배타적으로 만든다(`mkdir`, 있으면 실패) | 이름이 겹치면 실패(무작위 성분이 있어 실제로는 없다) |
| 6 쓰기 | 파일을 배타적 생성으로 쓰고 원본을 복사하며 digest 표와 대조. 텍스트는 UTF-8·LF(ADR-0007) | `source_changed`, I/O 오류. 폴더를 지우려 시도 |
| 7 검사 | 11절, 파일 목록 = 복사 계획 + 생성 파일 | `leak_found`·`inspection_failed`·`package_check_failed`. 폴더를 지우려 시도하고 위치를 알림 |
| 8 manifest | `publication.json`을 마지막 파일로 쓰고 다시 읽어 검사 | 같다 |
| 9 결박 | journal 잠금 안에서 `publication` 이벤트(9.3). 커밋 지점이다(ADR-0006 §6). 시작한 뒤에는 폴더를 지우지 않는다 | 예외·중단이면 8.4대로 이벤트를 다시 읽는다. 있으면 완료, 없으면 `publication_not_recorded`(`binding: unbound`), 읽지 못하면 같은 오류(`binding: unknown`). 폴더는 남고 `--list`가 `incomplete`(`no_completion_event`)로 보이며 #21은 게시하지 않는다 |
| 10 ZIP | 결박과 대조한 폴더에서 결정적 ZIP(9.6) | build는 완료, ZIP 실패만 알림. `--zip-of`로 다시 만든다 |

- **새 이름으로 옮기지 않는 이유.** 결박 이벤트가 커밋 지점이고 결박 없는 폴더는 어떤 경우에도 게시되지 않는다(ADR-0009 결정 6). 그래서 "폴더가 있으면
  완성"이라는 성질이 #20에 필요 없다. manifest를 마지막에 쓰므로 manifest 없는 폴더는 한눈에 미완료다. 이름 바꾸기를 쓰려면 ADR-0009가 #21에 맡긴
  `AtomicFiles.rename_new` 포트 확장(세 구현의 계약 시험 포함)을 앞당겨야 한다.
- build 자체의 재시도 보장은 없다(ADR-0006 §8: 베타의 재시도 보장은 Report 저장과 게시뿐). 다시 실행하면 새 `publication_id`의 새 build다.

### 9.3 `publication` 이벤트(제안)

```json
{"schema": "analytics.event/2", "seq": 413, "id": "e413", "recorded_at": "2026-10-07T15:40:12.481+09:00",
 "kind": "publication", "actor": "human",
 "text": "Built publication pub-01k6x8q2m4… (read; records up to e410)",
 "refs": [{"type": "report_revision", "report_id": "q3-review", "revision_id": "01J…",
           "relation": "includes", "basis": "declared", "version_binding": "author",
           "digest": {"sha256": "<Report manifest의 sha256>"}},
          {"type": "context_version", "version": "20261007-153000-000000_ab12cd34ef56.md",
           "relation": "includes", "basis": "observed", "version_binding": "author",
           "digest": {"sha256": "<CONTEXT 버전 파일의 sha256>"}}],
 "details": {"publication_id": "pub-01k6x8q2m4…", "package_sha256": "<publication.json의 sha256>",
             "content_sha256": "<10.4>", "format": "analytics.publication/1",
             "profile": {"id": "read", "rules_version": 1}, "scope": "project",
             "basis": {"as_of_seq": 412, "content_seq": 410,
                       "context_version": "20261007-153000-000000_ab12cd34ef56.md"},
             "analysis_state": {"value": "in_progress", "basis": "declared"},
             "exclusions": [{"type": "run", "run_id": "20261001-091000-region-summary"}],
             "location": ".analytics/publications/pub-01k6x8q2m4…",
             "checks": {"format": 1, "result": "pass",
                        "inspection": {"inspected": 41, "pinned_scanned": 3, "not_inspectable": 0}}}}
```

- 예에서 e411·e412는 앞 build들의 `publication` 이벤트다. `basis.as_of_seq`(412)는 ADR-0006 §5의 뜻대로 이 build가 읽은 마지막 seq이고,
  `content_seq`(410)는 6.1의 내용 기준이다.

- `refs`에는 포함한 Report 버전과 기준 CONTEXT 버전만 넣는다. 포함한 run·Visualization을 모두 넣으면 M 규모에서 한 줄이 수백 개 참조가 되고,
  맥락 블록은 프롬프트마다 journal을 읽는다(현재 계약 G "비용"). 항목 단위의 포함 사실은 manifest의 `included`·`files[].sources`에 있다.
- 요청한 제외의 정확한 ID(`exclusions`)는 프로젝트 기록인 이 이벤트에만 있고 패키지에는 범주별 개수만 간다(OQ6).
- `location`은 프로젝트 상대 경로다. 프로젝트 밖으로 낸 ZIP 경로는 적지 않는다(기기마다 다른 경로, ADR-0009 결정 3과 같은 이유).
- `details`의 필드와 `publication_id` 패턴은 기록 schema와 예제에 더한다. 기록 형식 변경이므로 측정 기록 대상이다([검증 증거의 기준](../PROJECT-OPERATIONS.md#검증-증거의-기준)).
- Workspace Timeline은 이 이벤트를 "Publication built"로 보인다(#13 뒤 `/2` 목록 표시를 따른다). 맥락 블록의 핵심 정보에서 게시 계열 kind를
  빼는 일은 #13·#16의 표시와 맞춘다(R13).

### 9.4 `publication_id`와 참조 읽기

- 형식: `pub-` + 소문자 Crockford base32 ULID 26자(`^pub-[0-9a-hjkmnp-tv-z]{26}$`). 디렉터리 이름이 될 수 있고, 80비트 무작위 성분이 있으며,
  소문자뿐이라 대소문자를 무시하는 대상에서도 겹치지 않는다(ADR-0009 결정 9). 시각 성분은 사람이 폴더를 보기 편하라고 둘 뿐이고 순서에 쓰지
  않는다. schema의 `opaque_id`(대소문자 혼용 허용)보다 좁힌다.
- reader(`relations._resolve`, 지금 `relations.py:1101-1102`는 `unsupported`)는 참조 type `publication`을 이렇게 판정한다(OQ20): 대상 기록은
  `details.publication_id`가 같은 `publication` 이벤트다. 없으면 `missing`(`record_absent`), 여럿이면 `ambiguous`, 참조의 `digest`가
  `details.package_sha256`과 다르거나 없으면 `missing`(`digest_mismatch`), 그 밖에는 `resolved`다. 공통 역사적 참조 경계를 지키고,
  모르는 형식이나 필수 결박을 해석할 수 없으면 `unsupported`다(ADR-0014 결정 6). build 폴더는 게시한 뒤 지워도 되는 전달 준비물이라
  보존 파일로 보지 않는다. 폴더가 있는지는 참조 판정이 아니라 `--list`의 폴더 상태다.
- 이 권장안이면 폴더가 지워지거나 바뀐 publication 참조도 `resolved`다. 그래서 Workspace와 `analytics_status`가 `resolved`를 "패키지가 온전함"으로
  읽히게 두지 않도록 그 참조 옆에 `--list`의 기록·폴더 상태를 함께 보인다. ADR-0006 §4의 5단계는 publication을 따로 정하지 않으므로 이 판정은
  ADR-0014 결정 6에 §4의 예외로 채택했다(OQ20). 실제 reader·표시는 PR-3 범위다.

### 9.5 최신과 더 오래된 build(ADR-0009)

- build 순서는 `publication` 이벤트의 journal seq다(ADR-0009 결정 5). manifest는 이 seq를 담지 않는다. 담으려면 이벤트를 먼저 써야 해서 커밋
  규칙과 어긋난다. #21은 프로젝트 journal에서, 다른 기기의 되돌리기는 `latest.json` 항목에서 순서를 읽는다.
- **build 잠금(OQ17).** 읽기부터 결박까지 한 프로젝트의 build를 하나씩 돌린다. 그러면 뒤 build의 `as_of_seq`는 앞 build의 결박 seq 이상이고,
  `content_seq`는 앞 build의 `content_seq`보다 작지 않다(같으면 그 사이에 게시 계열 이벤트만 있었고, 같은 요청·renderer면 내용도 같다). 잠금이
  없으면 늦게 시작해 먼저 끝난 build B 뒤에 먼저 시작한 build A가 결박되어, 더 오래된 기록의 A가 "더 새 build"로 최신이 될 수 있다.
- 잠금은 기존 `FileLocks.exclusive`(지금 상한 없이 기다림)다. 대기 상한은 ADR-0009 결정 5가 #21에 맡긴 `FileLocks` 확장이므로, #21이 먼저 들어오면 같은
  규칙(넘으면 `build_busy`)을 쓰고 그 전에는 기다린다(R18). `--preview`는 잠금을 잡지 않는다. 아무것도 쓰지 않고 결박하지 않으므로 build 순서와
  무관하고, 읽기의 일관성은 relations의 재시도와 미리 계산한 digest 표가 맡는다.
- 공개 명령에 `--as-of`가 없는 이유도 같다. 과거 기준으로 새로 만든 build가 더 큰 seq를 받아 최신이 되는 길을 열지 않는다.
- build는 게시하지 않는다. 더 오래된 build도 결박이 있으면 게시할 수 있고, 최신 전환(`not_newer`)과 `--switch-to`는 #21이 판정한다.
- 같은 Report v1을 나중에 다시 build하면 새 `publication_id`와 더 큰 seq를 받는다. Report 내용 버전은 그대로이고 복사한 파일의 해시도 같다
  (PUBLISH §3). 같은 기준의 반복 build는 channel에 내용이 같은 두 버전을 만들 수 있다. `content_sha256`으로 알 수 있고 막지는 않는다.

### 9.6 ZIP

- 결박된 폴더를 결박과 대조한 뒤 만든다. manifest와 manifest가 적은 파일만 담는다. 최상위 디렉터리는 `<publication_id>/`이고 그 안에 `index.html`과
  `README.txt`가 있다.
- 결정적 바이트: 항목은 경로의 코드 포인트 순, 디렉터리 항목 없음, 시각 1980-01-01 00:00:00 고정, 권한 `0o100644`, `create_system` 3, 이름은
  UTF-8 플래그(bit 11), extra field·주석·uid/gid 없음, deflate와 고정 수준. 빌드 기기의 사용자·시각이 ZIP에 남지 않는다. 다른 zlib 판에서는
  압축 바이트가 다를 수 있다. ZIP은 운반 사본이고 정체성은 안의 `publication.json` 해시다.
- 경로는 사람이 준다. 프로젝트 안이면 거절한다(받은 경로와 링크를 끝까지 푼 위치 모두로 비교, ADR-0009 결정 1과 같은 방법). 이미 있으면 덮어쓰지 않고
  거절한다(`zip_path_invalid`). 대상 이름을 배타적 생성으로 바로 쓰고, 실패·중단하면 지우려 시도한다. 남은 조각은 `--verify`가 잡는다. 없을 때만
  옮기는 연산이 #21의 `rename_new`로 생기면 임시 이름에 쓰고 옮기는 방식으로 바꿀 수 있다.
- 쓴 뒤 다시 열어 `verify_package`로 manifest와 대조한다.

## 10. 결정성과 manifest

### 10.1 결정적이어야 하는 것

같은 내용 기준(`content_seq`·`context_version`), 같은 요청(프로필과 `rules_version`, 범위, 제외, 선언), 같은 renderer(kit 판과 표시 라이브러리 판),
같은 build 때 관측이면 내용 파일의 집합과 바이트가 같다(PUBLISH §4 재빌드 비교).

1. 내용 파일에 벽시계·난수·`publication_id`·`as_of_seq`·호스트·사용자·절대 경로·`source_revision`·`data-build`·`?v=`가 없다. 시각은 기록의 값이다.
2. 모든 목록을 명시적 키로 정렬한다. 디렉터리 나열 순서에 기대지 않는다.
3. kit이 쓰는 텍스트는 UTF-8(BOM 없음)·LF다(ADR-0007). JSON은 키를 정렬하고 공백 규칙을 고정한다. 사용자 글은 정규화하지 않는다.
4. `content_seq` 규칙(6.1, OQ15). `as_of_seq`는 정체성 파일에만 있다.
5. `code.css`는 build마다 생성하고 mtime에 기대지 않는다.
6. 표시 라이브러리(plotly.js, pygments, markdown-it-py)는 프로젝트 환경의 판을 따른다(wheel에 의존성 없음). 판을 manifest `renderer`에 적고, 다른
   판끼리는 같은 바이트를 약속하지 않는다.
7. 내용은 기록과 build 때 관측의 함수다. 기록이 같아도 그사이 파생 표가 덮어써졌다면 `content_changed`가 생겨 내용이 바뀐다. 재빌드 비교가 알려야
   하는 차이다.

### 10.2 정체성 파일

build마다 다를 수 있는 값(`publication_id`, `built_at`, `basis.as_of_seq`)은 `publication.json`, `about.html`, `assets/identity.js` 세 파일에만 둔다. 페이지의 띠에는 결정적
내용만 HTML로 있고, `identity.js`가 그 옆 칸에 ID와 시각을 채운다. JS가 꺼진 독자는 "About this copy" 링크로 `about.html`에서 본다(OQ16).

### 10.3 `publication.json`(형식 `analytics.publication/1`)

ADR-0009 결정 9의 필수 내용(자기 자신을 뺀 파일의 경로·바이트 수·sha256, entry, 빌드 시각)에 PUBLISH §4의 후보를 더한다.

| 필드 | 내용 |
|---|---|
| `schema`, `publication_id`, `built_at` | 형식(새 가족 이름, 용어집 §3에 먼저 등록), 정체성, 빌드 시각(offset 포함) |
| `entry` | `"index.html"` |
| `basis` | `as_of_seq`(ADR-0006 §5의 뜻: 이 build가 읽은 마지막 seq), `content_seq`와 그 이벤트의 `recorded_at`(`content_recorded_at`, 6.1), `context_version`. 모르면 null(ADR-0006 §5) |
| `analysis_state` | `{"value": …, "basis": "declared"}`(OQ7) |
| `ui_locale` | `"en"` |
| `profile` | `{"id": …, "rules_version": …}` |
| `scope` | 범위 종류, 포함한 Report 버전(`report_id`·`revision_id`·Version n·기준 시점 상태·manifest sha256·파일 대응 `{path, sha256, package_path}`) |
| `included` | 포함한 대상의 `ItemKey` 목록(원래 기록과의 대응, PUBLISH §4) |
| `not_included` | 범주·사유별 개수. ID가 없다(OQ6) |
| `reader_features` | 실제 제공하는 기능(`read`, `relations`, `downloads`, `interactive_figures`). `search`는 없다(OQ21) |
| `renderer` | kit 판, 패키지 형식 판, 번들 라이브러리(이름·판·sha256·라이선스) |
| `remote_links` | 패키지 안의 원격 이동 링크 목록. #21 receipt가 옮긴다(ADR-0009 결정 9) |
| `limits` | 해시 없이 복사한 파일 수, 원격 자원·스크립트·형식 때문에 빠진 파일, 이미지 메타데이터 키(파일별 GPS·작성자·일련번호, OQ25), 일반 경로 모양 경고(OQ24), 포함 기록 제외 필드의 긴 줄 경고 등 |
| `checks` | 검사기 형식 판, 돌린 검사와 결과, 검사 상태별 파일 수(`inspected`·`pinned_scanned`·`not_inspectable`, 11.5) |
| `content_sha256` | 10.4 |
| `files[]` | 자기 자신을 뺀 모든 파일의 `path`·`bytes`·`sha256`, `origin`(`generated`·`bundled`·`copied`·`identity`), 판정한 `format`과 `inspection`(11.5), 사본이면 `sources`(`ItemKey`·역할·기록 해시·`verified`), 가공본이면 `derived_from` |

`project_id`, 빌드 기기, 프로젝트 경로, `source_revision`은 넣지 않는다. 원래 기록과의 대응은 `publication_id`로 프로젝트 journal에서 찾는다.

### 10.4 두 해시와 비교

- **패키지 해시** `package_sha256` = `publication.json` 바이트의 sha256. ADR-0009 결정 6의 결박 값이고 build마다 다르다.
- **내용 해시** `content_sha256` = 정체성 파일 셋(`publication.json`, `about.html`, `assets/identity.js`)을 뺀 `files[]`의 `{path, bytes, sha256}`을
  `path`의 코드 포인트 순으로 정렬해 RFC 8785 정규 JSON으로 만든 바이트의 sha256(ADR-0006 §8과 같은 정규화). 비교 전에 manifest의 `renderer`가 같아야
  뜻이 있다(OQ16).

## 11. 패키지 검사

검사는 `publishing/checks.py`(수준 1)에 있다. build의 7·8단계, `--preview`, `--verify`, ZIP 뒤, 시험, #21의 확인·최종 확인이 같은 함수를 쓴다
(ADR-0009 결정 9 "같은 규칙").

### 11.1 참조 규칙(ADR-0009 결정 9)

- 대상: 모든 `.html`(Report 본문과 사용자 HTML 포함), `.css`, `.svg`. 파서는 stdlib `html.parser`와 작은 CSS 토크나이저다.
- 자원 참조: `src`, `srcset`, `<link href>`, `poster`, `<object data>`, `<embed src>`, `<iframe src>`, `data-payload`, SVG `href`·`xlink:href`,
  `style` 속성과 `<style>`의 `url()`·`@import`, CSS 파일의 `url()`·`@import`. 정규화한 뒤 패키지 밖으로 나가지 않는 상대 경로나 `data:`만 허용한다.
  `data:`는 허용하되 그 안의 바이트를 11.5대로 풀어 다시 검사한다.
- 이동 링크: `<a href>`, `<area href>`. 패키지 안 상대 경로나 `http`·`https`·`mailto`만 허용하고 원격 링크는 `remote_links`에 모은다.
  패키지 안 이동 링크는 파일을 가리킨다(`…/index.html`). 디렉터리를 가리키는 링크(`…/`)는 kit 결함이고 정적 크롤러도 실패로 본다. `file://`에서는
  디렉터리 주소가 `index.html`로 열리지 않기 때문이다(ADR-0009 결정 4가 channel 페이지에 같은 규칙을 둔 근거).
- 늘 금지: `../`로 패키지 밖, 루트 절대 URL(`/x`), `//host`, `file:`, 드라이브·UNC, `<base>`, `<meta http-equiv="refresh">`(channel 페이지는 #21의
  것이다), `javascript:` URL, `<form action>`.
- kit이 만든 바이트가 어기면 renderer 결함이므로 build가 실패한다. 사용자 내용(Report 본문, 사용자 HTML·SVG)이 어기면 7.5의 처리를 따른다.
- 실행 중 스크립트가 만드는 참조는 정적 검사 밖이고 12.6의 브라우저 시험이 맡는다(ADR-0009 결정 9).

### 11.2 이름

모든 패키지 경로가 ADR-0003 결정 6의 이름 규칙(예약 장치명, `<>:"|?*`와 제어 문자, 끝의 `.`·공백)을 지키는지, NFC인지, casefold로 겹치지 않는지,
링크·빈 디렉터리가 없는지, 경로 길이가 상한 안인지 본다. 가장 긴 경로를 출력에 적는다. kit이 만든 이름이 걸리면 kit 결함(`package_check_failed`,
7.2)이고, Report `content/` 경로가 걸리면 그 Report 버전의 `report_not_shareable`(사유 `path`)이다. 기록에 남은 이름 자체를 이유로 멈추지 않는다
(ADR-0003 결정 6).

### 11.3 파일 일치와 `verify_package`

- 패키지 안을 가리키는 모든 참조가 manifest의 파일 하나와 대소문자까지 맞는다(build 쪽 누락 0).
- manifest의 모든 파일이 있고 바이트 수와 sha256이 같다. manifest 밖의 파일, 링크, manifest의 파일을 담지 않는 디렉터리가 없다. 예외는 OS·동기화 프로그램 메타데이터의 닫힌
  목록이며 ADR-0009 결정 6의 목록을 같이 쓴다.
- 필수 파일(kit 자원, 고른 Report의 모든 파일, 프로젝트 범위의 기준 CONTEXT)이 원본에서 없거나 다르면 build는 끝나지 않는다. 증거 파일이 원본에서 없는
  것은 기록의 사실이므로 그 항목을 `missing`으로 보이고 계속한다(PUBLISH §4 "필수 파일이 누락되면 불완전한 빌드를 완료로 표시하지 않는다").
- `verify_package(path, binding=None)`은 폴더나 ZIP을 받는다. ZIP이면 항목 이름(`..`·절대 경로·역슬래시 없음), 고정 시각·권한, extra field·주석
  없음, 항목 목록 = manifest, 각 바이트의 sha256을 본다. `binding`을 주면 manifest 해시를 결박과 대조한다.

### 11.4 누출 검사의 금지 목록

공통 코드(`publishing/scope.py`)가 스냅샷, 공통 닫힘(5.2), 원장에서 만든다. Strategy가 만들지 않는다. 포함 집합은 "원장 ∩ 공통 닫힘"이고,
금지 목록의 대상은 "스냅샷 − 포함 집합"이다. 원장이 닫힘 밖의 항목을 "포함"으로 적어도 그 항목은 금지 목록에 남는다.

- **포함 대상의 완료 이벤트는 그 대상에 딸린다.** 포함한 run의 `run` 이벤트, 포함한 데이터셋의 `import`, 포함한 Report 버전의 `report_revision`,
  기준 CONTEXT의 `context` 이벤트는 OQ3의 종류 목록 밖이지만 "포함하지 않은 기록"으로 세지 않는다. 그 ID와 글을 금지 목록에 넣으면 포함한
  대상의 이름 때문에 오탐이 난다. 그 글(예: `run` 이벤트의 `제목 — 상태 (error.summary)`, `runner.py:1018-1020`)은 패키지에 나오지 않는다.
- **제외 토큰**: 포함 집합 밖의 기록의 식별자와 이름. 포함 집합의 허용 필드나 kit label 표에 있는 토큰은 뺀다(같은 스크립트의 포함 run과 제외
  run은 같은 스크립트 경로를 가진다).
  - 강한 토큰(어디서든 찾는다): `run_id`, `script::id`, `report_id`·`revision_id`, 경로 구분자가 든 프로젝트 상대 경로, CONTEXT 버전 파일 이름,
    8자 이상의 데이터셋 이름.
  - 약한 토큰(이름 자리에서만 찾는다): 짧은 데이터셋 이름, 확장자만 있는 파일 이름, `e<seq>`. 패키지 경로, `href`·`src`·`id`·`download`, payload의
    키와 경로 같은 값에서만 찾는다. 본문에서 찾으면 오탐이 많다.
- **제외 문장**: 포함 집합 밖의 이벤트 글에서 공백을 정리한 24자 이상의 줄. 토큰처럼 포함 집합의 허용 필드나 kit label 표에 같은 줄이 있으면
  뺀다. 그대로 옮긴 문장만 잡는다(바꿔 말한 것은 한계, 2.4).
- **포함 기록의 제외 필드**: 환경을 가리키는 값(`env.platform`, `request_id`, `request_fingerprint`, `session`)은 금지 목록에 넣는다. pid처럼
  8자보다 짧은 값은 아래 하한 규칙대로 쓰지 않는다. stdout·stderr·오류 글(`error.message`·`error.summary`·hint·`warnings`·`finalize_errors`)의
  24자 이상 줄은 포함한 그림이 같은 값을 정당하게 담을 수 있으므로 build를 멈추지 않고 미리보기·`limits` 경고로 둔다.
- **로컬 환경**: 프로젝트 루트(기록에 쓰인 철자, 받은 경로와 끝까지 푼 경로, NFC·NFD, POSIX·Windows 구분자, `file://` URL, 퍼센트 인코딩, JSON의
  `\/`·`\\` escape), 홈 디렉터리, OS 사용자 이름이 든 경로 구성(`/Users/<u>/`, `/home/<u>/`, `C:\Users\<u>\`), venv 경로, 임시 디렉터리, 홈 아래에
  있는 이 경로들의 `~/` 형태, 호스트 이름(`socket.gethostname()`·`platform.node()`), 기록에 남은 모든 절대 경로(밖의 입력, 가져오기
  `source.path`, `env.executable`), aelix session 값. 8자보다 짧은 표식은 쓰지 않고 그 수를 경고로 적는다.
- **대조 전 정규화**: URL 속성값과 `data:` URI는 퍼센트 인코딩을 풀고, 대조하는 글과 금지 목록 양쪽을 NFC로 정규화한다.
- **일반 패턴(OQ24)**: `file://`, 드라이브 문자 절대 경로, UNC, `/Users/`·`/home/`으로 시작하는 경로, `~/`로 시작하는 경로. 다른 기기가 기록한
  경로도 잡는다. 이 패턴은 이 기기의 값도 기록에 남은 경로도 아닌 글에서 오탐이 많다: 웹 분석 데이터의 URL 경로(`/home/pricing`,
  `/Users/{id}/settings` API), 표시 시험 글(`C:\Windows\…`). 그래서 권장안(OQ24)은 이 패턴을 중단이 아니라 경고로 둔다. 이 기기의 환경 값과
  기록에 남은 절대 경로는 위의 로컬 환경 목록이 따로 잡아 멈춘다.

### 11.5 형식 판정과 풀기

패키지의 모든 파일(생성·번들·사본·정체성)은 확장자가 아니라 처음 바이트(magic byte)와 엄격한 UTF-8 해석으로 형식을 판정한다. 판정한 형식이
아래 표에 있고 그 파일의 역할에 허용되면 풀어서 검사한다. 그렇지 않으면 검사할 수 없는 파일이다. 파일마다 검사 상태를 manifest
`files[].inspection`과 `checks`의 상태별 수, 미리보기, 범위 구역에 남긴다.

| 검사 상태 | 뜻 |
|---|---|
| `inspected` | 표의 방법으로 모든 문자열을 풀어 금지 목록과 대조했다 |
| `pinned_scanned` | 번들 파일. 원시 바이트에서 로컬 환경 값과 강한 토큰을 찾았고, 추가 조건으로 출처 고정(kit 자원이나 plotly 패키지 파일과 같은 바이트)을 확인했다 |
| `not_inspectable` | 형식을 풀 수 없다. 원칙적으로 패키지에 넣지 않는다(아래) |

| 형식(판정 근거) | 허용 역할 | 검사하는 값 |
|---|---|---|
| HTML(kit 생성, 7.5를 통과한 사용자 HTML, Report 본문) | generated, figure, report_file | 원래 바이트, `html.parser`로 푼 텍스트 노드·모든 속성 값(entity 해제)·주석, 11.1의 참조. URL 속성값은 퍼센트 인코딩을 풀고, `data:` URI는 base64·퍼센트 인코딩을 푼 바이트를 이 표로 다시 판정해 검사한다 |
| CSS | generated, report_file | 원래 바이트, `url()`·`@import`. `data:` URI는 HTML과 같다 |
| JS payload(`assets/figures/*.js`) | generated | 감싼 부분을 떼고 JSON으로 파싱한 모든 키와 문자열(escape 해제) |
| 번들 JS·CSS(`publication.js`, `plotly.min.js` 등) | library | 원시 바이트에서 로컬 환경 값과 강한 토큰. 출처 고정은 추가 조건이다. `plotly.min.js`는 사용자가 바꿀 수 있는 프로젝트 환경에서 오므로 출처 고정만으로 믿지 않는다 |
| PNG(`89 50 4E 47 0D 0A 1A 0A`) | figure, report_file | 청크 종류와 `tEXt`·`iTXt`·`zTXt`(압축 해제)·`eXIf`의 값, 모르는 보조 청크의 원시 바이트 |
| JPEG(`FF D8 FF`)·GIF(`GIF87a`·`GIF89a`)·WebP(`RIFF`…`WEBP`) | figure, report_file | APP1(EXIF·XMP)·COM, GIF comment·application extension, WebP `EXIF`·`XMP ` 청크 |
| SVG(XML, 루트 `svg`) | figure, report_file | XML 파싱: 텍스트, 속성, 주석, `<metadata>`, 7.5의 조건. `data:` URI는 HTML과 같다 |
| plotly figure(JSON) | figure(가공본) | 모든 문자열(`hovertemplate`·`customdata`·`text`·`meta`·`layout.images[].source` 포함), 원격 자원 trace·layout |
| Parquet(앞뒤 `PAR1`) | attached_data, report_file | `pyarrow`(`publishing/extract.py`)로 key-value 메타데이터(UTF-8로 풀고 JSON이면 파싱), `ARROW:schema`는 base64를 풀어 Arrow schema로 읽고 중첩 field의 이름과 field metadata까지, 열 이름(중첩 포함), 문자열·binary 값(struct·list·map·dictionary 안 포함), 통계(min·max) |
| UTF-8 텍스트(코드·Markdown·텍스트·CSV) | code_snapshot, report_file | 엄격한 UTF-8로 읽은 글 |
| `publication.json` | identity | 허용한 필드만 있는지(schema)와 문자열 값 |
| ZIP(운반 사본) | — | 11.3 |
| 그 밖(PDF `%PDF`, Office와 그 밖의 ZIP 기반 형식, gzip, 모르는 바이너리, UTF-8이 아닌 텍스트) | 없음 | `not_inspectable` |

- **kit이 고른 역할**(그림·첨부 표·코드 스냅샷)의 파일이 그 역할의 허용 형식이 아니면 복사하지 않고 그 항목을 `not_included`(사유 `format`)로
  둔다. `analytics.visualize(path)`는 확장자를 가리지 않고 복사하므로(`capture.py:363-370`, `_figure_kind`) CSV·PDF·XLSX "그림"이 있을 수 있고,
  그 바이트는 read에서도 원시 데이터를 담을 수 있다. 확장자만 그림인 파일(`.png`로 저장한 CSV)도 판정으로 거른다.
- **Report 버전**의 파일이 `not_inspectable`이면(PDF·Office·그 밖의 바이너리) OQ23의 권장안대로 build를 멈춘다(`report_not_shareable`, 사유
  `format`, 파일과 형식). Report는 버전 단위로만 넣기 때문이다(OQ9).
- **fail closed.** 허용 형식인데 추출기가 없거나(`pyarrow`를 가져오지 못함) 파싱이 실패하면 그 파일을 통과시키지 않고 build를 멈춘다
  (`inspection_failed`, 파일과 형식. 바로잡는 길은 `--exclude`나 추출기 설치). `data:` URI의 중첩은 정해 둔 깊이(구현 PR이 정함)까지 풀고,
  넘으면 `inspection_failed`다.
- 그래서 권장안대로면 패키지에 `not_inspectable` 파일이 없다. 다른 답으로 그런 파일이 들어가면 manifest·미리보기·범위 구역에 남기고, 그 build는
  14절 "#20-2" 행의 통과 증거가 되지 못한다.
- 남는 한계: 표의 방법 밖의 인코딩(사용자 글 속의 base64 문자열, 이미지 픽셀 속의 글자)은 보지 못한다. 범위 구역과 현재 계약 G에 적는다.

### 11.6 결과

| 발견 | 처리 |
|---|---|
| 닫힘을 넘은 원장(5.2) | build 중단(`leak_found`, 원인 `projection_out_of_scope`). 아무것도 쓰기 전이다. 넘은 항목의 범주별 수. Strategy 결함이다 |
| 제외 토큰(강한 것, 또는 이름 자리의 약한 것)·제외 문장·포함 기록의 환경 값 | build 중단(`leak_found`). 위치(패키지 경로, 줄·JSON 위치), 원본(아래), 바로잡는 길(그 항목을 `--exclude`, 대상을 포함, 새 Report 버전, 기준 CONTEXT면 `CONTEXT.md`를 고쳐 새 버전으로 남김. 기준 CONTEXT는 `--exclude`로 뺄 수 없다) |
| 이 기기의 로컬 환경 값, 기록에 남은 절대 경로 | build 중단. P0에는 가린 사본이 없다(OQ13) |
| 일반 경로 모양(OQ24) | 권장안: 계속. 미리보기·`limits`·범위 구역에 위치와 함께 경고 |
| 포함 기록 제외 필드의 긴 줄(11.4) | 계속. 미리보기·`limits` 경고 |
| 검사할 수 없는 형식(11.5) | kit이 고른 역할이면 그 항목을 `not_included`(`format`)로 두고 계속, Report 파일이면 중단(`report_not_shareable`, OQ23), 추출 실패는 중단(`inspection_failed`) |
| 링크 규칙 위반, manifest 밖 파일, 빈 디렉터리, kit이 만든 이름의 이식성·충돌 | build 중단(`package_check_failed`) |
| 이미지 메타데이터(금지 값 없음) | 계속. `limits`에 키 목록, 미리보기와 범위 구역에 파일별 GPS·작성자·일련번호 키 경고(OQ25) |

**원본 짚기.** 누출 검사는 두 단계다. 먼저 4단계에서 모델의 `Text`를 `source`와 함께 금지 목록과 대조한다. 걸리면 원본(포함 항목과 필드)을
짚어 알린다. 다음 7단계에서 패키지 바이트를 대조한다. 사본 파일의 발견은 복사 계획의 항목(`ItemKey`·역할)이 원본이다. 생성 파일에서 바이트
단계에만 걸린 것은 모델에 없던 글이므로 renderer 결함(원인 `renderer`)으로 보고한다. 미리보기도 같은 두 단계를 돈다.

검사 결과를 사람이 승인해 넘기는 옵션은 두지 않는다(OQ12). 바로잡는 길은 범위나 내용을 바꾸는 것이다.

### 11.7 표면별 예방·검사·시험

시험 열의 canary는 12.2의 행렬이다. "양성"은 포함 쪽 canary가 패키지에 있어야 한다는 뜻이고 검사기 자신의 시험이다.

| 표면 | 예방(투영·렌더) | 검사 | 시험 |
|---|---|---|---|
| HTML 글 | 허용 필드만 모델에, renderer는 모델만 | 텍스트 노드 | 제외 이벤트 글·run 제목·데이터셋 이름 canary 부재, 포함 쪽 양성 |
| 속성·tooltip | 포함 집합 밖 대상은 상태와 관계없이 이름·tooltip 없음(C12) | 모든 속성 | `title`·`alt`·`aria-label`·`data-*`·`download` 안의 canary 부재 |
| 범위 밖 상태 | C12, `Text.source` | 텍스트·속성 | 범위 밖 `file_absent` 데이터셋(8자 미만 이름)의 이름 부재 |
| 원장 | 공통 닫힘 확인(5.2) | 금지 목록 = 스냅샷 − (원장 ∩ 닫힘) | 적대적 투영(12.1) → `leak_found`, 결박 없음 |
| payload | inline payload 없음, 한 직렬화기 | JSON 파싱 | 키·값의 canary 부재, `</script>`·U+2028 escape |
| plotly figure | 포함한 Visualization만, 그림당 한 파일 | 모든 문자열, 원격 trace | 포함 그림의 `customdata`·`meta`에 심은 제외 run ID → `leak_found` |
| 데이터 다운로드 | `evidence`의 첨부 표만 | Parquet 메타·열·값 | 제외 데이터셋 canary 부재, 첨부 표 key-value 메타의 로컬 경로 → 중단 |
| 이미지·SVG | 포함 그림만, SVG는 `<img>`로만 | 청크·세그먼트·XML | PNG `tEXt`·JPEG COM·SVG 주석에 심은 경로 → 중단, matplotlib PNG 통과 |
| 형식 | magic byte 판정, 역할별 허용 형식(11.5) | 모든 파일의 검사 상태 | CSV·PDF·XLSX 그림과 `.png`로 저장한 CSV → `not_included`(`format`), 압축된 Report 파일 → 중단, 중첩 Parquet 문자열·`ARROW:schema` field metadata의 canary → 중단, 추출 양성 대조 |
| `data:` URI | 허용하되 다시 판정 | 퍼센트·base64를 풀어 11.5 | base64 `data:` 안의 제외 canary → 중단, 포함 canary 양성 |
| 번들 자원 | kit 자원과 plotly 패키지 파일만 | 원시 바이트 + 출처 고정 | `plotly.min.js` 사본에 심은 프로젝트 경로 → 중단 |
| 사용자 HTML | 7.5 조건 | HTML, 원격 참조 | 스크립트가 든 HTML 그림이 `not_included`, 통과한 HTML의 iframe에 `allow-scripts` 없음 |
| 코드 | `evidence`만, 해시가 같을 때만 | 텍스트 | 코드 주석의 제외 경로 → 중단 |
| 로그·환경·telemetry·session | 허용 목록 밖 | 로컬 환경 목록, 포함 기록의 환경 값 | stdout·stderr·`env.executable`·`env.platform`·`request_id`·session·호스트 이름·telemetry `summary` canary 부재 |
| CONTEXT 이력 | read는 기준 본문만 | 텍스트 | 지운 줄 canary: read 부재, evidence 존재(양성) |
| 파일 이름·anchor·ID | kit 이름 `<k>`와 내용 해시로만(7.2) | 패키지 경로와 `id` | 제외 run의 slug 부재, casefold 충돌 없음, `con`·`nul` dataset 이름의 build 성공 |
| 원본 기록 사본 | 복사 계획에 기록 역할 없음 | 이름·출처 | `run.json`·`*.meta.json`·`journal.jsonl` 부재 |
| 남은 파일 | 새 폴더, writer만 씀 | 목록 = manifest | 폴더에 손으로 넣은 파일 → 중단 |
| 숨긴 내용 | CSS 숨김으로 제외하지 않음 | — | `Absent from latest run`·`<details>` 안의 제외 canary 부재 |
| 실행 중 참조 | 네트워크 API 없음, `data-payload`만 | 출하 JS 정적 검사 | 출하 JS에 금지 이름 없음, 네트워크를 막은 E2E의 외부 요청 0 |
| manifest | 범주별 개수만 | schema와 값 | 제외 ID 부재 |
| ZIP 메타 | 결정적 기록 | 11.3 | 두 build의 ZIP에서 정체성 파일 말고 같은 항목 바이트 |
| 검색 인덱스 | P0에 없음(OQ21) | — | `reader_features`에 `search` 없음 |

## 12. 검증과 시험

### 12.1 계약 시험(`tests/test_contracts.py`)

지금 포트 표와 같은 형태로 `PROJECTIONS` 표(`read`, `evidence`, `recording`)를 더하고 구현마다 4.3의 C1–C13을 돌린다. 표에는 지원 능력(범위 종류)을
적고 지원하지 않는 능력의 사례는 이유와 함께 건너뛴다(건너뜀은 통과가 아니다). C4는 두 실제 구현에만 돈다. 필드 포괄 시험(4.3)도 여기 둔다.
build 전체를 하위 프로세스에서 `sys.addaudithook`의 `open` 이벤트로 무장해 돌리고, 복사 계획·kit 자원·`plotly.min.js`·기록 파일(읽기 단계) 밖의
경로를 열면 실패하게 한다("단일 바이트 통로"의 증거).

**적대적 투영.** 계약 표 밖의 시험용 구현 `adversarial`은 `--exclude`로 뺀 run을 원장에 "포함"으로 적고 그 run의 canary를 모델에 담는다. build와
`--preview`는 `leak_found`로 끝나고 결박이 없어야 한다. 닫힘 확인과 따로, 금지 목록 조립 함수에 같은 원장을 넣으면 그 run의 토큰이 목록에
남고, 그 목록으로 돌린 바이트 검사가 같은 canary를 잡아야 한다. 금지 목록이 원장만 믿지 않는다는 증거다.

### 12.2 canary 행렬

- 시험 도우미가 새 프로젝트(#41 S fixture의 사본이나 손 fixture)에 kit의 실제 writer로 고유 canary(`CANARY-<표면>-<incl|excl>-<n>`)를 심는다:
  이벤트 글, run 제목, 스크립트 경로와 주석, 차트 제목·caption, plotly `hovertemplate`·`customdata`·`layout.meta`, PNG `tEXt`, SVG 주석,
  Parquet 열 이름·값·key-value 메타, stdout·stderr, 오류 메시지, `env.executable`, `env.platform`, `request_id`, session, telemetry `summary`,
  CONTEXT의 지운 줄, 데이터셋 이름과 `source.path`, Report 본문(제외 버전과 포함 버전), 포함한 발견 글 속의 제외 run ID.
- 범위 넷(프로젝트, 보고서 하나, 묶음, 프로젝트+`--exclude`) × 프로필 둘(+시험용 `recording`)마다 build하고, 패키지의 모든 바이트(11.5로 푼 값 포함,
  원시·HTML escape·JSON escape·퍼센트 인코딩 형태)와 ZIP을 푼 항목에서 제외 canary가 0회이고 포함 canary가 기대한 표면에만 있는지 본다.
- "포함 글 속의 제외 ID"·"포함 그림 속의 제외 ID" 사례는 `leak_found`로 끝나고 결박이 없어야 한다.
- **형식(음성).** 압축된 Report 파일(FlateDecode 스트림 안에 제외 canary를 둔 PDF, DOCX)은 OQ23 권장안대로 `report_not_shareable`(`format`)로
  끝난다. 중첩 Parquet 문자열(struct·list 안의 값, `ARROW:schema`의 field metadata)과 base64 `data:` URI 안에 둔 제외 canary는 `leak_found`다.
  CSV·PDF·XLSX "그림"과 `.png`로 저장한 CSV는 read·evidence 모두 `not_included`(`format`)이고 그 바이트가 패키지에 없다. `pyarrow`를 가져오지
  못하게 한 evidence build는 `inspection_failed`다. 번들 `plotly.min.js` 사본에 심은 프로젝트 경로는 중단이다.
- **형식(양성 대조).** 포함 쪽 canary를 PNG `zTXt`, Parquet 중첩 값, `ARROW:schema` field metadata, base64 `data:` URI 안에 두고, 검사기가 추출한
  문자열에서 그것을 찾아야 한다. 추출이 실제로 일어났다는 증거다. 음성 사례만으로는 추출기가 아무것도 풀지 않아도 통과한다.
- **오탐.** 포함 기록의 글에 든 웹 분석 URL 경로(`/home/pricing`, `/Users/123/settings`)는 OQ24 권장안대로 경고로 끝나고 build는 끝난다. 포함
  run의 완료 이벤트 글과 포함 데이터셋의 `import` 글은 금지 목록에 들지 않는다.
- **범위 밖 상태(C12).** 포함한 발견이 가리키는 범위 밖 데이터셋의 파일을 지운 프로젝트에서 그 데이터셋 이름(8자 미만 포함)이 패키지에 없다.
- canary는 #41 생성기에 넣지 않고 시험 쪽에서 덧입힌다. 생성기 manifest의 digest와 사례 표를 바꾸지 않기 위해서다.
- 이것이 Q14 "선택하지 않은 원시 데이터·세션·로컬 경로·로그가 숨겨진 자원으로 남지 않음"의 기계 증거다.

### 12.3 #41 fixture

- S(`tools/eval_fixtures.py scale --scale S --seed 0`): read·evidence build의 전체 흐름. `unpreserved_data`의
  `content_changed`·`missing`·`unknown`이 구별되고 바이트가 복사되지 않는다. `same_chart_id`의 미결박 참조가 최신으로 잇지 않는다.
  `correction`·`withdrawal`(기준 시점 상태), `reference_cycle`(관계 재생성이 끝남), `long_korean`(한국어 글 보존, `lang=""`).
- `display_safety`는 `note` 하나에 HTML처럼 보이는 글과 경로 글을 함께 담는다(`tools/eval_fixtures.py:1211-1217`). 그래서 사례를 나눈다.
  - read: `note`는 OQ3 권장안에서 read에 없다. 그 글은 패키지에 없고 범위 구역의 개수에만 있다. read의 escape는 같은 HTML 글을 담은 손 fixture의
    발견으로 확인한다.
  - HTML처럼 보이는 글(`<script>`·`onerror`·`javascript:`·템플릿·주석): evidence에서 escape된 글로 보이고 링크·자원·실행이 되지 않으며 링크 규칙을
    통과한다.
  - 경로 글: 누출 정책을 따른다. `../../etc/passwd`는 상대 경로라 패턴이 아니다. `C:\Windows\System32\drivers`는 일반 패턴(11.4)이므로 OQ24
    권장안이면 경고와 함께 evidence build가 끝나고, "중단"을 고르면 evidence build가 `leak_found`로 끝난다. 시험은 답에 맞춰 기대값을 고정한다.
- fixture는 지금 `/1`이다. #20 구현 때는 #13 뒤의 `/2` 생성을 쓰고 결박 시험은 `/2`에서만 한다.

### 12.4 Report 사례(PUBLISH §8)

v1 build 뒤 로컬 v2 저장 → v1을 다시 build해도 Report 파일 sha256이 같고 v2 내용이 없다(범위 구역은 "A later version existed when this copy was
built — not included"만. build 때의 관측이다, PUBLISH §3).
같은 v1의 두 build는 다른 `publication_id`와 같은 Report 파일 해시, 같은 기준이면 같은 `content_sha256`. 일부 구성원만 든 묶음(#19가 묶음을 만들면).
자유 본문에 제외 대상이 남으면 `leak_found`. 철회된 버전은 `--allow-withdrawn` 없이 거절(OQ10). 스크립트·원격 참조가 든 Report HTML은
`report_not_shareable`(OQ8). 통과한 HTML 본문은 `allow-scripts` 없는 sandbox로 보인다. 한국어·Unicode 경로의 Report 파일이 이름 그대로(NFC) 패키지와
ZIP에 들어간다. 하위 디렉터리 entry(`content/sub/report.md`가 `img/a.png`와 `../data.csv`를 상대 경로로 가리킴)의 링크·이미지가 보기 페이지에서
깨지지 않는다(7.5 Report 보기 계약). PDF·Office 파일이 든 Report 버전은 `report_not_shareable`(`format`, OQ23)이다.

### 12.5 결정성·원자성·이식성·계층

- 같은 프로젝트를 두 번 build(두 번째는 첫 번째의 `publication` 이벤트 뒤)하면 `content_seq`, `content_sha256`, 모든 내용 파일 바이트가 같고
  `publication.json`·`about.html`·`identity.js`만 다르다. 두 번째 build의 `basis.as_of_seq`는 첫 build의 결박 seq 이상이다. 기록 하나를 더하면
  `content_seq`와 내용이 달라진다. `content_seq` 규칙 자체(닫힌 집합, 모르는 kind·`unsupported`·복구한 줄을 건너뛰지 않음)는 손으로 쓴
  `publication` 줄 fixture로 PR-1에서 시험한다.
- 각 단계 경계에서 중단을 주입한다: 결박 전 중단은 결박 없음과 폴더 삭제 시도, manifest 뒤 결박 전은 `unbound`, 결박 뒤 ZIP 실패는 build 완료.
  9단계에는 두 사례를 더한다. (a) `publication` 줄을 다 쓴 뒤 반환 전의 중단(예외 주입): 다시 읽어 완료로 알리고 폴더가 남는다. (b) 끊긴 줄(줄의
  앞부분만 쓰고 실패): `publication_not_recorded`(`unbound`), 폴더가 남고 reader가 그 줄을 `damaged`로 보인다. 두 사례 모두 폴더 삭제가 없다.
- 동시 build 둘: 잠금으로 직렬화되고 결박 seq 순서와 기준 순서가 같다.
- 이름: 기록에 남은 예약 장치명 dataset 이름(`con`·`nul`), `_`가 든 view 이름, casefold로만 다른 `revision_id` 둘, NFC·NFD 경로를 가진 프로젝트가
  build되고, 패키지에는 kit 이름(`<k>`)으로 들어가며 원래 이름은 글로 보인다. `<k>` 충돌을 주입하면 `package_check_failed`. 긴 경로(Windows).
- 스냅샷 일관성: 읽기 반환 뒤, 투영 전, 복사 중에 Report content와 코드 스냅샷 파일을 바꾼다. 판정은 스냅샷의 digest 표대로이고, 복사 중의
  변경은 `source_changed`다.
- 미리보기와 build: `CONTEXT.md`를 고친 프로젝트(로컬 경로를 넣은 것과 넣지 않은 것)에서 미리보기와 build가 같은 CONTEXT 내용을 검사하고 같은
  결과(둘 다 `leak_found`, 둘 다 통과)를 낸다. 미리보기 뒤 프로젝트에 파일·기록·잠금 파일 변화가 없다.
- 계층: 새 모듈 등록, `share_render` → `core` import가 실패하는 의도적 위반 사례.
- 실행·모델 없음: runner와 네트워크를 실패하도록 무장한 채 build한다(Q16).

### 12.6 독자 환경

| 확인 | 방법 | 어디서 |
|---|---|---|
| 원본 없는 위치 | 패키지를 한국어와 공백이 든 임시 경로로 복사하고 원본 프로젝트를 지운 뒤 아래를 모두 한다 | 세 OS CI |
| `file://` 정적 순회 | stdlib 크롤러가 entry에서 모든 상대 링크와 자원 참조(`data-payload` 포함)를 따라가 파일이 있는지, 패키지 밖으로 나가지 않는지 본다. 디렉터리를 가리키는 이동 링크는 실패다(11.1) | 세 OS CI(모든 레그) |
| HTTP 하위 경로 순회 | 상위 폴더를 `python -m http.server --bind 127.0.0.1`로 내보내고 `/a/b/<publication_id>/index.html`처럼 두 단계 하위 경로에서 순회한다. 모든 요청이 그 하위 경로 안이고 200이다. 자원 해석의 증거이지 접근 권한의 증거가 아니다(ADR-0009 '구현에서 확인할 것' 1) | 세 OS CI(모든 레그) |
| ZIP 풀기 | Python `zipfile`, `unzip`(Linux·macOS), `Expand-Archive`(Windows), `ditto -x -k`(macOS)로 풀어 manifest와 대조. 한국어 이름 포함 | 세 OS CI |
| 브라우저 E2E | 로컬 밖 요청을 막은 컨텍스트에서 `file://`과 HTTP 하위 경로로 모든 페이지를 연다. 요청 목록(패키지 밖 0)·콘솔 오류, 그림이 그려졌는지, 발견 → 당시 Visualization → run → (evidence) 코드 → 뒤로 가기로 같은 anchor 복귀(최신 버전으로 가는 이동이 있으면 실패, 베타 기준 §4), sandbox iframe의 스크립트가 `parent`에 닿지 못함, 원격 자원 그림 fixture의 외부 요청 0 | Linux Chromium 필수, Firefox·WebKit은 라벨·수동(OQ22) |
| JS 꺼짐 | 같은 페이지를 JS 없이: 띠·관계 목록·링크·"needs JavaScript" 글 | E2E와 같다 |
| 이 사본에 없음 | 이미지 하나·그림 payload 하나·CSS를 지운 사본과 `index.html`만 다른 곳에 둔 사본(ZIP 안에서 연 경우의 대리): "Missing from this copy"와 CSS 없음 안내. HTML 그림 파일과 Report iframe 문서를 지운 사본: 그 iframe 옆의 대체 글이 보인다(페이지가 스스로 판정하지 못하는 한계, 7.6) | E2E, `--verify` 단위 시험 |
| 출하 JS 정적 검사 | 금지 이름(7.4)이 없고 script 요소를 만드는 함수가 하나뿐 | 단위 시험 |
| 언어 | `tests/test_language.py`를 넓혀 패키지 페이지에서 `lang=""` 밖의 한국어가 없음 | CI |
| OS 도구로 ZIP 열기 | Windows 탐색기, macOS 보관 유틸리티 | 소유자. 하지 않았으면 `not_run` |
| 실제 다른 독자 | 소유자가 다른 기기나 계정으로 연다(ADR-0009 결정 10) | #24. #20의 몫이 아니다 |

로컬 링크 검사를 실제 게시 대상의 열람 증거로 쓰지 않는다(PUBLISH §8).

### 12.7 규모

#41 M fixture(`scale --scale M --seed 0`)에서 build 시간, 패키지 크기, 페이지 수, 가장 큰 페이지, 누출 검사 시간을 같은 `environment` 기록과 함께
잰다. "Load figure" 게이트 값과 타임라인 쪽 크기도 여기서 정한다. 게이트 수치가 아니라 기록이고, 베타 지원표에는 실제 잰 범위만 적는다.

### 12.8 증거 기록

`publication` 이벤트의 `details`와 `analytics.publication/1`은 새 기록 형식이므로 PR-3이 측정 기록(`docs/verification/`의 md+JSON)을 만든다.
측정한 커밋 뒤 문서가 아닌 변경이 생기면 병합 전에 다시 잰다. 그 밖의 PR은 PR 본문의 검증 표가 증거다.

## 13. PR 나눔

| PR | 시점 | 내용 | 리뷰 | 끝났다는 증거 |
|---|---|---|---|---|
| PR-0 결정 | #13·#18 뒤, #19 대기 없음 | ADR-0014의 `publication` 이벤트 `details`·`analytics.publication/1`·ID·참조 예외·`content_seq`, ADR-0006 연결, 용어집 등록, PUBLISH §4·§5·현재 순서 보완(구현 전 표시 유지) | 결정 PR이며 그 리뷰가 기록 형식의 설계 리뷰다(독립 1·Codex 1) | 문서 검사, 리뷰 처분 |
| PR-1 투영 | PR-0 채택·#13·#18 병합 뒤. #19 대기 없음 | `model.py`·`projection.py`·`scope.py`(공통 닫힘), `ShareSnapshot`(미리 계산한 digest 표), `content_seq` 규칙과 손으로 쓴 `publication` 줄 fixture의 결정성 시험, 상태 대응, 관계 재생성, 계층 등록, 계약 시험 C1–C13, 필드 포괄 시험, 모델 수준 canary | 일반(독립 1·Codex 1) | 세 레그 CI, 계약 시험 표 |
| PR-2 패키지·검사·미리보기 | PR-1 뒤 | `render.py`, loader·writer, `checks.py`(링크·이름·형식 판정·`verify_package`·누출·7.5 판정), `extract.py`, `analytics build --preview`(기록·파일을 쓰지 않음, 잠금 없음), 적대적 투영, 패키지 수준 canary 행렬과 형식 음성·양성 fixture, 정적 크롤러(`file://`·HTTP), 결정성 시험 | 일반 | CI, canary 결과 표 |
| PR-3 build와 결박 | PR-2 뒤 | 실제 build 폴더, build 잠금, manifest, `publication` 이벤트와 reader의 `publication` 판정, 9단계 실패의 다시 읽기(8.4), `--list`·`--verify`, 템플릿 `.gitignore`와 #13 전환의 무시 규칙, 현재 계약(현재 동작과 G "반출 정책·가림")·README "현재 공유 범위", 용어집 등록 | 기록 형식 PR: 구현 리뷰(독립 1·Codex 1)와 수정 diff 확인(설계 리뷰는 PR-0) | CI, 측정 기록(md+JSON) |
| PR-4 Report 범위 | PR-3·#19 뒤 | `--report`·`--collection`·`--scope`, 철회 처리, Report 보기 페이지(#19의 `ReportRenderer` 주입과 7.5의 계약), Report HTML·SVG의 7.5 처리, 검사할 수 없는 Report 파일(OQ23), 12.4 사례 | 일반 | CI, 시험 표, Q16의 #20 몫 결과표 |
| PR-5 ZIP과 독자 E2E | PR-4 뒤 | 결정적 ZIP·`--zip`·`--zip-of`, OS 도구로 풀기, 7.6 누락 표시, 브라우저 E2E job(OQ22, Report 보기 포함), CSP(측정 뒤), M 규모 측정 | 일반 | CI 실행 링크, Q14의 #20 몫 결과표 |

- PR-2의 `--preview`는 결박 없이도 쓸모가 있다. 공유 범위와 누출 검사를 기록 형식 PR보다 먼저 써 보고 고치기 위해 일부러 나눈다. 미리보기는
  아무것도 쓰지 않고 결박하지 않으므로 build 잠금이 필요 없다(9.5). 그래서 잠금은 PR-3에 둔다.
- PR-1·PR-2는 사용자에게 보이는 기록이 없어 #18·#19의 화면 설계가 바뀌어도 되돌리는 비용이 작다.
- #21 구현은 #20 PR-3의 결박·manifest·`verify_package`가 병합된 뒤 시작할 수 있다(2026-10-17 소유자 결정,
  [#21](https://github.com/handochan/aelix-analytics/issues/21)). Report·ZIP은 #20 PR-4·PR-5에서 이어 가고 #20 전체 수용은 그때까지 미완료다.
  `verify_package`·결박·링크 규칙은 #21이 그대로 쓰도록 `publishing/checks.py`의 함수로 둔다.
- #19와 겹치는 ADR-0006·`relations.py`는 먼저 병합한 계약을 뒤 PR이 반영한다. #19의 새 기록 필드는 PR-1 필드 포괄 시험이
  공유 여부 결정을 요구하게 하고 기본 투영에는 자동 포함하지 않는다. Workspace 파일은 #20 renderer가 고치지 않는다.
- 이름 변경·파일 이동·기록 의미 변경을 한 PR에 섞지 않는다(베타 기준 §5).

## 14. 요구 추적표

| 요구 | 장치(절) | 시험(절) | PR |
|---|---|---|---|
| #20-1 read/evidence와 선택한 Report 버전에 실제 공유 범위 | 4, 5 | 12.1 C1·C3·C4, 12.4 | 1, 4 |
| #20-2 HTML·payload·다운로드·이미지에 제외 내용 없음 | 2, 5.2, 5.3, 11.4–11.7 | 12.1 적대적 투영, 12.2 canary 행렬과 형식 음성·양성 fixture. 패키지에 `not_inspectable` 파일이 있는 build는 이 행의 통과 증거가 아니다(11.5) | 2 |
| #20-3 인터넷 없는 file·HTTP 하위 경로·누락·외부 의존 | 7.4–7.6, 11.1, 11.3 | 12.6 크롤러·E2E·누락 사본 | 2, 5 |
| #20-4 지금 데이터·최신 정의로 과거 근거를 바꾸지 않음 | 5.4, 6 | 12.1 C2·C6, 12.3 `unpreserved_data`·`same_chart_id` | 1, 2 |
| Q14 원본 없는 위치에서 열기 | 7, 11 | 12.6 원본 없는 위치 | 2, 5 |
| Q14 지표·차트·관계·근거 왕복과 같은 버전 복귀 | 5.5, 7.1, 7.4 | 12.6 E2E 왕복 | 5 |
| Q14 진행 중 상태·제외 범위가 보임(#20 몫: 기록 시점, 빌드 시각, 선언한 분석 상태, 범위) | 7.3, 10.2 | 12.6 JS 꺼짐, 화면 검사 | 3, 5 |
| Q14 게시 시점·최신 전환 시각이 보임 | #21의 channel 페이지·receipt(ADR-0009 결정 4·7). 패키지는 게시해도 바뀌지 않으므로 build에는 게시 시점이 없다 | #21의 증거 | `pending`(#21) |
| Q14 이미지·첨부·CSS·JS가 원본 경로에 기대지 않음 | 7.1, 11.1 | 12.5, 12.6 | 2 |
| Q14 숨겨진 자원 없음, 미포함 근거를 완전 재현 가능으로 표시하지 않음 | 2.3, 5.4, 7.3, 7.7 | 12.2, kit label 표 검사(재현 가능 주장 없음), 범위 구역의 빠진 코드·데이터 글, 첨부 표 label(요약·일부일 수 있음) | 2 |
| Q14 사용자 정의 차트의 지원 범위 | 7.5 | 12.6 E2E sandbox·원격 자원 그림 | 2, 4, 5 |
| Q16 선택한 Report 버전만, 버전 대응, 추가 LLM 없음 | 5.1, 6.2, 9.3, 10.3 | 12.4, 12.5 무장 시험 | 4 |
| Q16 임의 HTML의 격리 보기, 자유 본문에 남은 제외 대상 | 7.5, 11.6 | 12.4 | 4 |
| Q15 대체 Strategy, 조용한 대체 없음 | 4.3 C9·C11, 4.4 | 12.1 | 1 |
| ADR-0009 결정 9 manifest·결박·참조 규칙·이름·`publication_id` | 9.3, 9.4, 10.3, 11.1–11.3 | 12.5, 12.6 | 2, 3 |
| ADR-0009 결정 5 build 순서 | 9.5 | 12.5 동시 build | 3 |
| ADR-0006 §4 `not_included`와 원본 `missing`의 구별, 범위 밖 이름 없음 | 5.4 | 12.1 C3·C12 | 1 |
| ADR-0006 §5 `as_of_seq`의 뜻 유지 | 6.1, 9.3, 10.3 | 12.5 두 번 build | 1, 3 |
| ADR-0003 결정 6 기존 이름으로 멈추지 않음 | 7.2, 11.2 | 12.5 이름 | 2 |
| ADR-0001 확인 2 대체 구현의 같은 의미 | 4.3 C11 | 12.1 | 1, 3 |

## 15. 소유자 결정(2026-10-07, 권장안 모두 채택)

binding 입력이 정하지 않았거나 받아들인 보장(PUBLISH·보고서 설계의 문장)을 좁힐 수 있는 것만 적는다. 이름·문법·배치처럼 구현에 맡긴 것은 본문에서
정했다. 2026-10-07 소유자가 아래 권장안 25개를 모두 채택했다. 나중에 한 답을 바꾸면 "바뀌는 것" 열의 절을 고친다. OQ15는 새 기록 필드 하나의 결정이고 OQ20은 채택된 ADR(ADR-0006 §4)에 없는 판정이므로
PR-0의 설계 리뷰에서도 확인한다. OQ23–OQ25는 `7010c6e`의 설계 리뷰에서 나왔다.

| # | 질문 | 권장안 | 바뀌는 것 |
|---|---|---|---|
| OQ1 | `--profile` 없이 build하면 어느 프로필인가 | `read`. 결과에 포함·제외 요약을 늘 보인다 | 기본 build의 포함 목록. PUBLISH §5는 "첫 기본값 후보"로만 적었다 |
| OQ2 | read·evidence 경계의 세부: 로그, 오류 글, 환경, 데이터셋, 첨부 표, CONTEXT 이력 | stdout·stderr는 둘 다 제외. 오류는 둘 다 `error.type`·`line`과 상태만, 메시지·summary·hint·`warnings`·`finalize_errors` 글은 둘 다 제외(가린 사본이 없는 P0에서 절대 경로 때문에 build가 자주 멈추지 않게). 환경은 evidence에 `python`·패키지·`kit_version`만. 데이터셋은 evidence에 이름·행·열·schema·해시·설명, 미리보기·통계는 둘 다 제외. 첨부 표는 evidence에 파일과 행·열 수(HTML 미리보기 행 없음). CONTEXT는 둘 다 기준 본문, 버전 diff는 evidence만 | 5.3의 (OQ2) 칸, evidence의 누출 표면 수. 대안: 더 엄격하게 환경·diff도 제외하거나, 오류 글을 evidence에 넣고 누출 검사에 맡김(evidence build가 더 자주 멈춤) |
| OQ3 | 프로젝트 범위에 넣을 기록 종류 | 발견·결정·가정·질문·다음 할 일·분석 초점·지표 정의와 관측을 기준 시점 상태와 함께(정정·철회도 상태 표시로). 메모는 evidence만. Workflow feedback·Record coverage·Tool usage·재사용 후보·checkpoint·게시 계열은 둘 다 제외 | 5.1·5.3. PUBLISH §5 read 목록(목적·지표·질문·발견)에 없는 종류의 처리. #18 Insights 구역 가운데 공유되는 것 |
| OQ4 | 프로젝트 범위의 Visualization 집합 | 키마다 기준 시점의 최신 성공 버전과 포함한 기록·Report가 인용한 정확한 버전. `Absent from latest run` 키는 인용될 때만, 나머지는 개수만. 미리보기가 목록을 보이고 `--exclude`로 뺀다 | 탐색용 그림이 의도 없이 나가는지. "차트"(PUBLISH §5)가 갤러리 전체인지 인용한 것만인지 |
| OQ5 | P0에 항목 단위 제외(`--exclude`)를 넣는가 | 넣는다. 포함 쪽 선택은 범위(프로젝트·Report 버전·묶음)로만 | 누출 검사 실패의 탈출구. #20 완료 조건(프로필과 Report 버전)보다 넓은 기능 |
| OQ6 | `not_included` 대상의 ID·이름을 보이는가 | 보이지 않는다. 종류·사유와 범주별 개수만. 범위 밖 대상은 `missing` 등 다른 상태여도 같다(C12). 정확한 제외 목록은 프로젝트의 `publication` 이벤트에만 | 추적성(PUBLISH §4)과 누출(§5)의 선택. run ID에 스크립트 slug, 키에 스크립트 경로가 든다 |
| OQ7 | 분석 상태(진행 중·완료)는 어디서 오는가 | build의 사람 선언(`--analysis-state`). 기본 `in_progress`, `complete`는 명시할 때만. "declared"로 적고 새 기록 kind는 만들지 않는다 | manifest·띠·이벤트의 `analysis_state`. ADR-0006에 완료를 적는 기록이 없다 |
| OQ8 | 사용자 HTML·SVG(Visualization의 html, Report HTML)와 원격 자원이 필요한 그림(plotly 지도·geo, altair)을 P0에서 어떻게 다루는가 | 스크립트·이벤트 속성·원격 참조가 없는 것만 `allow-scripts` 없는 sandbox iframe이나 `<img>`로 넣는다. 나머지 Visualization은 `not_included`(`scripts`·`remote_resources`), 그런 파일이 든 Report 버전은 build 중단. 정적 이미지 변환은 P0 밖. 스크립트를 허용하는 옵션은 두지 않는다 | 7.5. altair 기본 HTML과 지도 그림이 P0 공유본에서 빠진다. 대안(스크립트를 허용한 sandbox)은 ADR-0009 결정 9와 오프라인·외부 요청 0의 약속을 지키지 못한다 |
| OQ9 | Report 버전을 파일 단위로 나눠 넣을 수 있는가, 자유 본문에 제외 대상이 남으면 | 버전 단위로만 넣는다. 본문·첨부가 검사에 걸리면 build 중단, 사람이 고친 새 Report 버전(#19)을 골라 다시 build. 공유 전용 가공 산출물은 P0 밖 | PUBLISH §5·보고서 설계 §7이 둔 "가공 산출물" 선택지를 P0에서 미룬다(받아들인 선택지를 좁힘) |
| OQ10 | 철회된 Report 버전을 넣을 수 있는가 | `--allow-withdrawn`을 줄 때만, 모든 자리에 "Withdrawn". `prepared`·`incomplete`는 버전이 아니므로 거절 | 8.1·8.2. ADR-0006 §6의 상태가 공유에서 갖는 뜻 |
| OQ11 | 보고서 중심 build가 `based_on.refs`를 어디까지 따라가는가 | Report 버전, `based_on.refs`의 결박된 대상, 그 대상의 부속(그림, evidence면 첨부 표·코드·입력 정보)까지. 대상이 다시 가리키는 기록은 `not_included`(`out_of_scope`). 목표·CONTEXT는 Report가 인용할 때만 | 5.2. PUBLISH §7 "공유하지 않은 프로젝트 내용을 자동 추가하지 않음"과 "포함된 근거로 이어짐" 사이의 깊이 |
| OQ12 | 누출 검사의 범위와 실패 처리 | 11.6대로: 닫힘을 넘은 원장, 제외 식별자·문장, 이 기기의 로컬 경로와 기록에 남은 절대 경로, 링크 규칙, manifest 밖 파일, kit 이름의 충돌은 build 중단. 일반 경로 모양은 OQ24, 검사할 수 없는 형식은 OQ23. 사람이 넘기는 옵션 없음. 사용자 내용 속 제외 식별자도 경고가 아니라 중단. 자격 증명 패턴 탐지는 P0 밖으로 한계에 적음 | 11.6. PUBLISH §5 "투영된 결과와 실제 패키지 모두를 검사"의 범위 |
| OQ13 | 포함한 바이트(코드·Report 본문·그림 문자열)에 로컬 절대 경로가 있을 때 가린 사본(`--mask-local-paths`)을 만드는가 | P0에는 두지 않는다. build는 멈추고 위치를 알리며, `--exclude`·`read`·새 Report 버전으로 빠져나간다. 멈춤 빈도를 M fixture와 #24 리허설에서 잰 뒤 후속 Issue로 정한다 | P0 evidence build가 경로가 든 코드에서 멈춘다(R4). PUBLISH §5 "가려진 코드 발췌는 원본과 구별"을 P0에서 미룬다 |
| OQ14 | 기록된 해시가 없는 `/1` 그림 파일·첨부 표를 넣는가 | 넣는다. "No recorded hash"를 보이고 manifest에 `verified: no_recorded_hash`. #13 뒤 해시가 생기면 대조 | #20 완료 조건 4를 `/1` 그림에서는 확인하지 못한다는 한계. 빼면 `/1` 프로젝트의 그림이 모두 빠진다 |
| OQ15 | 게시본의 내용 기준으로 새 필드 `content_seq`를 두는가 | 둔다(6.1). `basis.as_of_seq`는 ADR-0006 §5의 뜻(읽은 마지막 seq) 그대로 정체성 파일에만 두고, 띠와 내용 비교는 `content_seq`(`as_of_seq` 이하에서 게시 계열 닫힌 집합이 아닌 마지막 줄의 seq)를 쓴다. 용어집에 등록하고 PR-0의 ADR에 기록 형식 필드로 적는다 | 6.1, 7.3, 9.3, 10.3. 받지 않으면 PR-0을 ADR-0006 §5를 개정하는 ADR로 만들어야 하고(같은 이름이 reader와 게시본에서 다른 값), 그것도 하지 않으면 띠의 eN이 build마다 바뀌어 재빌드 비교가 늘 "다름"이 된다 |
| OQ16 | 재빌드 비교의 단위와 정체성의 자리 | `content_sha256`은 정체성 파일 셋을 뺀 파일. 페이지는 기준만 HTML로, `publication_id`·`built_at`은 `publication.json`·`about.html`·`identity.js`에만. 공개 `--as-of` 없음 | JS를 끈 독자는 `about.html` 링크로 빌드 시각을 본다. PUBLISH §4(재빌드 비교)와 ADR-0009 결정 9(빌드 시각)의 조정 |
| OQ17 | 한 프로젝트의 build를 직렬화하는가 | 한다. 읽기부터 결박까지 build 잠금. 대기 상한은 #21의 `FileLocks` 확장과 같은 규칙 | 9.5. 동시 build가 기다린다(사용자에게 보이는 동작) |
| OQ18 | build 출력과 ZIP의 위치·보존 | 폴더는 `.analytics/publications/<publication_id>/`(git 무시, run 출력 비교 밖), 자동 삭제 없음. ZIP은 사람이 준 프로젝트 밖 경로, 덮어쓰지 않음 | 9.1·9.6. 새 저장 경로이고 템플릿·#13 전환의 `.gitignore`가 바뀐다 |
| OQ19 | build가 읽기 전에 CONTEXT 버전을 남기는가 | 남긴다. render·record·import처럼 `core.sync_context`를 부른다. `--preview`는 남기지 않고 대기 버전을 메모리에서 기준으로 삼아 같은 검사를 한다 | 6.3. build가 결박 말고 `context` 이벤트 하나를 더 쓸 수 있다. 대안 1: 쓰지 않고 "CONTEXT.md has changes not recorded as a version"만 경고(기준이 오래된 CONTEXT일 수 있음). 대안 2(리뷰 제안): 프로젝트 범위에서만 동기화. Report 범위는 CONTEXT를 Report가 인용할 때만 담으므로 Report와 무관한 CONTEXT 편집이 `content_seq`와 내용을 옮기지 않는다 |
| OQ20 | 참조 type `publication`의 판정에서 build 폴더를 보존 파일로 보는가 | 보지 않는다. 이벤트의 존재와 `package_sha256`으로 판정하고 폴더 상태는 `--list`가 보인다(9.4). 이 답이면 Workspace·`analytics_status`가 그 참조 옆에 `--list`의 기록·폴더 상태를 함께 보여 `resolved`를 "패키지가 온전함"으로 읽히게 두지 않고, PR-0의 ADR에 ADR-0006 §4의 개정으로 적는다 | ADR-0006 §4 5단계 "보존 파일"의 해석(§4는 publication을 따로 정하지 않음). 대안: 폴더가 없으면 `missing`(`file_absent`), manifest가 다르면 `content_changed` |
| OQ21 | P0 패키지에 검색을 넣는가 | 넣지 않는다. `reader_features`에 `search`가 없다. 검색은 #15가 인덱스를 정한 뒤 후속 Issue | PUBLISH §4 "모든 내부 링크·검색·관계 탐색은 패키지 안에서"의 검색 부분을 P0에서 미룬다. 검색 인덱스라는 누출 표면이 없다 |
| OQ22 | 브라우저 E2E 도구와 CI 비용 | #18이 도입한 도구를 쓴다. 없으면 Playwright(Python)를 개발 의존성으로 더하고 별도 CI job. Linux Chromium 필수, Firefox·WebKit은 라벨·수동·주간, Safari·Edge의 실제 확인은 소유자의 #24 | 12.6. 새 개발 의존성과 CI 시간(wheel 의존성은 그대로, ADR-0004 결정 1) |
| OQ23 | 검사할 수 없는 형식의 파일을 P0에서 어떻게 다루는가(그림이 PDF·CSV·XLSX인 경우, Report 버전에 PDF·Office·기타 바이너리가 든 경우) | kit이 고른 역할(그림·첨부 표)에서 미지원 형식은 자동으로 `not_included`(사유 `format`)로 둔다. 그런 파일이 든 Report 버전은 P0에서 build를 멈추고 파일과 형식을 짚어 알린다(`report_not_shareable`). 사람이 검사를 넘기는 옵션은 두지 않는다(OQ9·OQ12와 같은 원칙). PDF Report를 공유할 수 없는 비용은 #24 리허설 뒤에 다시 본다 | 11.5, 5.3. PDF·Office로 저장한 Report 버전과 비이미지 "그림"이 P0 공유본에서 빠진다. 대안(검사 없이 넣고 `not_inspectable`로 표시)은 "#20-2 제외 내용 없음"을 그 build에서 증명하지 못한다 |
| OQ24 | 이 기기의 환경 값도, 기록에 남은 절대 경로도 아닌 일반 경로 모양(`C:\Windows\…`, 웹 분석의 URL 경로 `/home/…`·`/Users/…`)을 포함한 사용자 글에서 만나면 build를 멈추는가, 경고만 하는가 | 이 기기의 환경 값과 기록에 남은 절대 경로에서만 멈춘다. 그 밖의 경로 모양은 미리보기·`limits`·범위 구역의 경고로 둔다(OQ12 보완) | 11.4, 11.6, 12.3. 대안(중단)은 다른 기기가 기록한 경로도 막지만 웹 분석 데이터와 표시 시험 글에서 evidence build가 자주 멈춘다(R3) |
| OQ25 | 사용자가 넣은 이미지의 메타데이터(EXIF GPS, XMP 작성자, 카메라 일련번호)를 어떻게 다루는가 | P0에서는 바이트를 바꾸지 않고 그대로 담는다(가공 없음). 미리보기와 범위 구역에서 파일별로 GPS·작성자 키를 경고한다. 금지 값이 있으면 지금 11.6대로 멈춘다 | 11.6, 10.3 `limits`. 대안(메타데이터를 지운 사본)은 원본 바이트와 해시가 달라져 가공본(OQ9)의 문제가 된다 |

## 16. 위험

| # | 위험 | 영향 | 줄이는 방법 | 남는 것 |
|---|---|---|---|---|
| R1 | 사용자 글이 제외 대상을 바꿔 말함 | 검사가 못 잡는 누출 | 미리보기에 포함한 글 목록, 한계를 화면·`limits`·현재 계약 G에 | 한계 |
| R2 | 포함 내용 속 비밀(토큰·키) | 고른 범위 안의 비밀 공유 | OQ12: P0 밖, 한계 표시 | 한계 |
| R3 | 약한 토큰과 일반 경로 패턴의 오탐·미탐 | 불필요한 중단, 짧은 이름의 누출 | 이름 자리에서만 찾음, 8자 하한, 범위 밖 대상은 이름 없음(C12), 일반 경로 패턴은 경고(OQ24), 포함 대상의 완료 이벤트를 금지 목록에서 뺌, 오탐 사례(웹 분석 URL 경로 `/home/…`·`/Users/…`, `C:\Windows\…` 표시 시험 글)를 시험으로 고정 | 본문 속 짧은 이름, 다른 기기가 기록한 경로는 경고뿐(OQ24) |
| R4 | 코드·Report 본문의 절대 경로로 evidence build가 자주 멈춤 | evidence 공유가 막힘 | 오류 글 제외(OQ2), `--exclude`·`read`·새 Report 버전, 실패가 파일과 줄을 짚음, M·#24에서 빈도 측정 뒤 OQ13 | 사람이 범위를 바꿔야 함 |
| R5 | build 폴더가 git에 들어감 | 비공개 데이터 commit | 템플릿·#13 전환의 무시 규칙, build의 경고 | 이미 전환한 프로젝트의 `.gitignore`는 #13 전환 몫 |
| R6 | `/1` 그림 파일에 기록된 해시가 없음 | 바뀐 그림을 과거 근거로 보일 수 있음. 복사 중의 변경도 알아채지 못함 | "No recorded hash"(OQ14), `/2` 해시(#13) | `/1` 프로젝트에 남음 |
| R7 | Workspace renderer와 공유 renderer가 따로 자람, #18 관계 계산이 `report.py` 안에 있음 | 화면 차이, 두 번 고침 | #18 리뷰에서 "관계 계산은 읽기 모델 쪽 순수 함수"를 요청, 순수 보기 함수를 낮은 계층에서 함께 씀, CSS 공유 | 일부 중복 |
| R8 | 허용 목록이 새 기록 필드를 놓침 | 독자가 정보를 못 봄(새지는 않음) | 필드 포괄 시험 | — |
| R9 | 과거 기준 build가 더 큰 seq로 최신이 됨 | 오래된 내용이 최신 주소에 | build 잠금, 공개 `--as-of` 없음(9.5) | 복사한 프로젝트끼리는 seq를 비교할 수 없음(ADR-0009) |
| R10 | 원격 자원 판정 누락(plotly의 새 지도 trace, 사용자 HTML) | 오프라인에서 빈 그림, 독자의 접속 정보가 외부로 | trace 목록, 7.5 조건, 네트워크를 막은 E2E | plotly 판을 올릴 때 목록 재확인 |
| R11 | 결정성이 라이브러리 판에 묶임 | 같은 기준인데 다른 해시 | manifest `renderer`, 같은 판끼리만 비교 | 다른 환경의 같은 바이트는 약속하지 않음 |
| R12 | 누출 검사 비용(M의 큰 첨부 표) | build가 느림 | M에서 측정, 필요하면 측정 뒤 열 통계·표본 검사로 바꾸는 결정 | 측정 전 미정 |
| R13 | `publication` 이벤트가 맥락 블록의 최근 기록을 차지 | 맥락의 정보 밀도 저하 | 핵심 정보에서 게시 계열 kind를 빼도록 #13·#16 표시와 맞춤 | 후속 Issue 후보 |
| R14 | `file://`의 엔진별 차이(script 불러오기, sandbox iframe, CSP `'self'`, 저장소 공유) | 어떤 브라우저에서 깨짐 | 세 엔진 E2E, CSP는 측정 뒤, 저장소 미사용 | Safari·모바일은 #24까지 `not_run` |
| R15 | plotly 그림은 그린 데이터 전체를 담음 | read에서도 그림의 점들이 공유됨 | "Interactive figures include the data they plot (N points)"를 미리보기·그림 옆·범위 구역·`about.html`에 | 사람이 판단 |
| R16 | 시각의 offset이 기기 시간대를 드러냄 | 작은 정보 노출 | 기록에 이미 있는 값만, 빌드 시각은 정체성 파일에만 | 수용 |
| R17 | `content_seq` 필드가 거절됨(OQ15) | 재빌드 비교가 늘 "다름"이거나 `as_of_seq`의 뜻이 reader와 게시본에서 갈림 | ADR-0006 §5를 개정하는 ADR을 PR-0에서 함께 검토 | 결정에 달림 |
| R18 | build 잠금이 #21 전에는 상한 없이 기다림 | 멈춘 build 뒤의 build가 끝없이 기다림 | 기다리는 동안 안내 한 줄, #21의 상한을 이어 씀 | #21 전까지 |
| R19 | 바쁜 프로젝트에서 `updating` 반복 | build가 끝나지 않음 | 안내와 재시도. 필요하면 측정 뒤 짧은 journal 잠금 안의 읽기를 검토 | — |
| R20 | 깊은 경로에 ZIP을 풀면 Windows 경로 길이 제한 | 일부 파일이 빠짐 | 경로 길이 상한의 경고, "Missing from this copy", `README.txt` | `not_run` |
| R21 | 독자가 스냅샷을 최신·검증된 결론으로 오해 | 잘못된 판단 | 모든 페이지의 띠, "declared" 분석 상태, latest·verified를 쓰지 않음 | — |
| R22 | #13·#17·#18·#19가 늦거나 모양이 다름 | 구현과 검증이 밀리거나 설계 수정 | PR-0은 먼저, 구현 PR을 시작할 때 다시 대조 | 일정 |
| R23 | 검사할 수 없는 형식의 Report(PDF·Office) | P0에서 그런 Report 버전을 공유하지 못함 | OQ23, 오류가 파일과 형식을 짚음, #24 리허설 뒤 재검토 | 비용 |
| R24 | 프로젝트 환경의 번들 라이브러리(`plotly.min.js`)가 바뀜 | 고친 라이브러리 바이트가 패키지에 들어감 | 원시 바이트 검사와 출처 고정, manifest `renderer`의 판·sha256 | 바뀐 동작(예: 외부 요청 코드)은 네트워크를 막은 E2E만 잡음 |
| R25 | `<k>` 이름이 원래 기록 ID와 다름 | 독자가 경로로 원래 기록을 찾기 어려움 | 페이지 글과 manifest `included`의 대응 | 수용 |

## 17. 범위 밖

- 게시·대상 폴더·channel·최신 주소·`--switch-to`·receipt(#21), 실제 다른 독자의 열람(#24), 독자 기기의 버전 전체 대조(#48).
- 프로젝트 인계 프로필, 자동 갱신, 피드백·협업(PUBLISH P2·P3), 원격 provider.
- 검색 인덱스(OQ21), 관계 그래프 JS(#18이 만들 때까지 텍스트 목록), 데이터 미리보기·통계, 실행 로그, 실행 파일 경로·플랫폼, 오류 메시지(OQ2).
- 가린 사본(OQ13), 공유 전용 가공 산출물(OQ9), 원격 자원 그림의 정적 이미지 대체(OQ8), 스크립트를 허용한 사용자 HTML, 검사할 수 없는 형식의
  공유(OQ23), 이미지 메타데이터 제거(OQ25).
- 두 번째 UI locale, 과거 기준으로 다시 build(`--as-of`), SDK 함수, 자격 증명 탐지, 이미 전달한 사본의 회수, 결박 없는 폴더의 정리 명령.

## 18. 근거와 실행하지 않은 것

- 이 문서는 설계 조사(같은 HEAD의 현황 지도와 독립 설계안 셋: 누출 안전 우선, MVP 우선, 독자 우선)를 합친 것이다. 누출 안전 우선안을 바탕으로
  MVP 우선안의 기준 seq 규칙(리뷰 뒤 `content_seq`로 바꿈)·수준 1 투영·이벤트 커밋 지점·내용 주소 사본·PR 나눔과 추적표, 독자 우선안의 스냅샷 띠·없음의 여섯 표시·독자 사본
  대책·사용자 HTML 규칙을 옮겼다.
- 조사의 정적 probe(데모 프로젝트, macOS, `6c4c2cd`) 결과: matplotlib PNG에는 텍스트 청크가 없었다(`capture.py:385`가 `Software`를 지움).
  첨부 Parquet의 key-value 메타는 `ARROW:schema` 하나였다. plotly figure 하나(8,253바이트)에 `customdata`·`meta`·URL은 없었고 `index.html`에 같은
  figure가 두 번 들어 있었다. 프로젝트 절대 경로는 run.json의 `env.executable`과 가져오기 기록의 원본 경로, Workspace의 Overview(1회)·데이터셋
  페이지(2회)에 있었다. 번들 plotly.js(v3.7.0)에는 지도용 원격 호스트 문자열이 있었다(동작은 재지 않음).
- 실행하지 않은 것(`not_run`): 브라우저로 연 것 전부(`file://`, HTTP 하위 경로, JS 꺼짐, sandbox iframe, iframe 불러오기 실패의 이벤트 동작,
  `<img>` SVG, CSP, 저장소 동작), plotly 지도·altair의 실제 원격 요청, 다른 OS, 시험 모음, #41 S/M fixture의 생성과 build 비용, 홈 디렉터리 아래
  프로젝트의 경로 노출, `pyarrow`로 `ARROW:schema`의 field metadata를 푸는 것, PDF·Office 파일의 형식 판정. 이 설계는 코드·문서 읽기와 위의 정적
  probe에만 기댄다. iframe이 불러오기 실패에도 `load`를 낸다는 것은 MDN 문서에 기댄 것이다.
- `7010c6e`의 설계 리뷰(독립 1·Codex 1)가 고칠 것 12개, 고려할 것 18개, 새 소유자 질문 3개를 냈다. 리뷰가 인용한 코드 줄(`capture.py:363-370`,
  `_platform.py:1247-1257`, `core.py:575-576`, `relations.py:287-289`·`553-570`·`812-820`, `runner.py:112`·`699`·`1018-1020`,
  `tools/eval_fixtures.py:1211-1217`, CONTRACTS.md:315)은 `6c4c2cd`에서 다시 읽어 확인했다.

## PR1–PR2 구현 증거의 범위

PR1의 고정 snapshot·모델·read/evidence·공통 닫힘은 PR117로 병합했다. PR2는 별도 renderer와 loader/writer·형식 추출기·독립 검사,
프로젝트에 쓰지 않는 build --preview를 연결한다. HTML/SVG/Plotly도 이제 형식·참조·누출 검사를 거치며 검사 결과/원본 소스와 파일별 제외를 기록한다.
기본 전략을 무장한 주입, 패키지 canary·형식 양성/음성·정적 file/HTTP·무쓰기 audit를 검증한다.
모델의 사용자 원문과 Text.source는 보존하고 독립 검사가 알려진 제외 값과 대조한다. 형식 판정은 비밀 탐지나 분석 결론 인증이 아니다.

실제 build 폴더/manifest/완료 이벤트는 PR3, Report/collection과 ReportRenderer 주입은 PR4, ZIP 운반·실제 브라우저/다른 독자·CSP·M규모는 PR5다.
그 사례는 미실행/미지원으로 구별한다. 현재 정적 Plotly 정책의 실행 증거는 프로젝트 환경의 plotly.py6.9.0/plotly.js3.7.0이며,
다른 표시 라이브러리 판·런타임 네트워크·브라우저 상호작용을 이 증거로 확대하지 않는다. 미리보기는 renderer 판을 응답에 남긴다.


## PR3 구현 구체화

실제 manifest의 `included`와 파일 provenance의 `item`은 ADR-0014 결정4의 package key(기존 `key_name`의 16자리 hex)를 쓴다.
내부 run/visualization 복합 키를 그대로 내보내면 기존 “run 제외 + 해당 run의 그림 선택”에서 제외 ID가 다시 노출되므로,
내부 모델·TypedRef를 바꾸지 않고 이미 페이지가 쓰는 결정적 대응을 manifest에도 적용한다. 원문과 scope 정책은 유지한다.

공개 CLI profile은 read/evidence다. C11의 등록된 recording 전략 주입은 실제 선택한 ID/rules_version을 manifest·완료 이벤트에
고정하며 새 공개 profile 설정이나 plugin API를 열지 않는다. Report/collection 요청은 PR4까지 명시적으로 거절한다.

PR3의 실제 I/O 경로에는 ZIP primitive와 명시적 --zip/--zip-of가 함께 구현된다(승인된 구현 지시의 순서 조정).
PR5의 OS 도구·브라우저/누락·CSP·M규모·Report 결합 검증은 미실행으로 유지한다. ZIP 성공을 독자 수용으로 쓰지 않는다.
읽기 전용 `publishing/state.py`는 공통 ReadModel과 completion helper를 받아 기록/폴더를 대조한다.
core/status는 이 낮은 경계만 소비하고 build/renderer에 의존하지 않는다. Workspace publication 관찰 조립은 #100 공유 UI 인계와 조율한다.

## PR4 구현 구체화

- 소유자 결정(2026-10-26): Report의 선택 metadata 가운데 summary·purpose·change_reason·content_status만 두 프로필에 담는다(OD1). scope·generation·
  알 수 없는 필드는 담지 않는다. 묶음은 구성원의 member_key·status·reason을 보이고 구성원별 scope는 담지 않는다(OD2). 5.3의 Report 행에 이 칸을 더한 것으로 읽는다.
- 인용된 다른 Report 버전은 고르지 않았으면 `not_included`(`out_of_scope`)다. 보고서 → 보고서를 따라가면 버전 단위 검사(OQ9)가 고르지 않은 버전에
  걸리기 때문이다. 묶음의 구성원은 고른 것으로 친다.
- 사본의 기준 CONTEXT 버전 이름(6.4)은 보고서 범위에서도 띠·manifest·완료 이벤트에 남는다(ADR-0014 결정 2·4). 본문은 Report가 인용할 때만 들어간다.
  누출 검사는 이 이름을 기준 값으로 허용한다.
- ReportRenderer는 진입점(kit CLI)이 Workspace의 기본 renderer를 주입한다. `publishing`은 renderer 구현을 import하지 않고, 주입이 없는데 Report가
  있으면 `renderer_unavailable`이다. renderer의 문서는 역할 `report_view`로 사용자 문서처럼 검사한다.
- 링크 검사(`resolve_link`)는 URL을 나눈 뒤 경로를 디코딩한다. Report 파일 이름의 `#`·`%`가 fragment·이스케이프로 읽히지 않게 하기 위해서다.
- 12.4의 사례는 `tests/test_publication_reports.py`가 덮는다. 브라우저 E2E·다른 독자·OS ZIP 도구·M 규모는 PR5다.

## PR5 구현 구체화

- **소유자 결정(2026-10-27)**: 측정을 보고 두 가지를 채택했다.
  - 사용자 HTML(Visualization의 html, Report HTML 본문)과 renderer 문서를 여는 iframe은 `sandbox="allow-same-origin"`이다. `allow-scripts`는 없다.
    `allow-scripts`·`allow-same-origin` 둘 다 없으면 세 엔진 모두 `file://`에서 frame이 자기 그림을 불러오지 못했다("Not allowed to load local resource").
    사용자 HTML의 스크립트·이벤트 속성은 검사가 이미 거절하고, frame 안에서는 스크립트가 실행되지 않는다. 검사기는 sandbox의 다른 값을 거절한다.
  - kit 페이지에 CSP meta를 둔다: `default-src 'self'; script-src 'self' 'sha256-<머리 inline script>'; style-src 'self' 'unsafe-inline';
    img-src 'self' data:; frame-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`. plotly가 inline style을
    쓰므로 `style-src`에 `'unsafe-inline'`이 있다. 세 엔진 × `file://`·HTTP 하위 경로에서 12쪽 모두 그림·plotly·CSS가 정상이고 CSP 위반이 없었다.
- 페이지 머리에 `<link rel="icon" href="data:,">`를 둔다. WebKit은 아이콘이 없으면 사이트 루트의 `/favicon.ico`를 요청해 HTTP 하위 경로 밖으로 나갔다.
- 7.6의 머리 inline script는 `publication.js`가 실행되지 않았으면 "could not load" 안내를 보인다. "Missing from this copy"는 `<html data-root>`로 푼 패키지 경로를 적는다.
- 5.2의 부속 간선(Visualization 버전 → run, run → 코드·입력/출력)을 공유 모델의 `attachments`로 옮겨 페이지가 링크한다(발견 → 그림 → run → 코드의 이동, 12.6).
- **CI**: 브라우저 E2E는 별도 job이 아니라 필수 검사인 `platform · ubuntu-24.04` 레그의 단계로 둔다. 저장소의 모든 CI job은 필수 검사라는 규칙(#22,
  `tests/test_ci_workflow.py`) 때문에, 별도 job은 ruleset 변경 없이는 병합을 막지 못한다. Playwright는 `browser` 의존성 그룹이고 그 단계만 설치한다(OQ22).
  Firefox·WebKit은 손으로 돌린다.
- **한계**: `<meta charset>`이 없는 사용자 HTML은 엔진이 다른 인코딩으로 읽을 수 있다(WebKit이 한국어 상대 경로를 잘못 풀어 404, Firefox는 경고). 사용자
  바이트는 바꾸지 않는다. iframe 문서가 빠진 것은 페이지가 판정하지 못한다(옆의 대체 글). OS의 보관 도구(탐색기·보관 유틸리티)로 연 확인과 실제 다른 독자는 소유자 몫이다.
