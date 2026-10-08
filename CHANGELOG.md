# 변경 이력

Aelix Analytics에서 사용자에게 보이는 변경을 적습니다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따릅니다.

**아직 어떤 버전도 게시하지 않았습니다.** 패키지 색인·버전 태그·GitHub 릴리스가 없고, wheel은 체크아웃에서 빌드합니다
([README](README.md#설치본wheel으로-쓰기)). 아래의 버전은 kit 버전(`KIT_VERSION`; `pyproject.toml`·manifest와 같음)이며, 새 프로젝트의
`lab.json`과 run의 `env.kit_version`에 남는 값입니다.

한 버전의 절은 main이 그 번호를 달고 있던 동안 병합된 변경입니다: 번호를 붙인 PR과, 그 뒤 번호를 올리지 않고 병합된 PR.
그래서 같은 번호를 적은 기록이라도 그 절의 어느 시점 코드가 썼는지는 run의 `env.kit_sha256`이나 commit으로 구별합니다.
날짜는 번호를 붙인 변경이 main에 들어간 날(KST)입니다. 동작의 자세한 차이는 [현재 계약](docs/CONTRACTS.md) F1절에 있습니다.

## [Unreleased]

없음. 병합된 변경은 그 PR이 병합될 때의 kit 번호 절에 적고, kit 번호를 올리는 PR은 새 절을 엽니다
([기여 가이드](CONTRIBUTING.md#첫-공개-전에-남은-운영-결정)).

## [0.7.0] - 2026-10-08

번호를 붙인 PR은 [#13]의 둘째 구현 PR(①, 표식 판정과 쓰기 게이트)인 [#80]입니다.

### Changed

- 프로젝트를 `analytics.json`과 `lab.json` 가운데 가장 가까운 표식으로 찾습니다(kit CLI, SDK, `analytics`, `bin/analytics`, aelix 확장).
  `lab.json` 프로젝트 아래의 `analytics.json` 디렉터리에서 부른 명령은 더 이상 위쪽 프로젝트에 쓰지 않습니다. 사람의 파일인 `analytics.json`
  (예: `data/exports/analytics.json`)도 그 디렉터리에서 찾기를 멈추게 하므로, 그 안에서 부른 쓰기는 위쪽 프로젝트 대신 `marker_invalid`로
  거절되고 hint가 그 파일이 사람의 것일 수 있다는 것과 진입점마다 프로젝트 루트를 고르는 법을 알립니다. `analytics`와 `bin/analytics`는 표식
  이름의 항목이 디렉터리가 아니면(FIFO·링크 포함) 거기서 멈추고 kit이 거기서 다시 위로 찾습니다. `open`은 kit처럼 어디로도 이어지지 않는
  링크를 표식으로 세지 않고 kit이 프로젝트로 삼는 디렉터리의 페이지를 열며, 두 표식이 다 있는 디렉터리에서는 이미 만든 페이지도 열지
  않습니다 ([#80]).
- 이 kit은 `lab.json`(`lab.project/1`) 프로젝트에만 씁니다. 새 형식(`analytics.json`, `analytics.project/2`)·모르는 형식의 표식은
  `project_too_new`, 표식이 아닌 `analytics.json`이나 읽지 못하는 표식은 새 오류 `marker_invalid`, 두 표식은 새 오류 `marker_conflict`로
  쓰기를 첫 부수 효과 전에 거절하고 프로젝트를 바꾸지 않습니다. 읽기는 두 표식만 거절하고, 쓰지 않는 프로젝트에서는 맥락 동기화·캐시·
  `queries.jsonl`·`telemetry.jsonl`·Workspace 렌더 예약을 건너뜁니다. 모르는 표식의 프로젝트도 이제 읽습니다(전에는 읽기도 거절) ([#80]).
- `status` JSON에 표식의 판정(`project.marker`·`format`·`writable`·`write_refusal`)과 `writes_skipped`가 생기고, 쓰지 않는 프로젝트의 글과
  맥락 블록이 `read only:` 줄로 시작합니다. `not_listed`는 없어졌습니다 ([#80]).
- `analytics.event/2` 기록이 목록·맥락 블록·Workspace에 `lab.event/1` 기록과 함께 seq 순서로 보입니다 ([#80]).
- 새 writer가 쓸 `analytics.run/2`(Visualization `visualizations[]`·`visualization_id`·`sha256`, `reproduction.visualizations`)와
  `analytics.dataset/1`(`data/imported/`)을 읽고, 두 run 형식의 Visualization과 재실행 비교를 모든 화면과 재실행 비교에서 같게 읽습니다.
  `data/imported/`의 데이터셋은 `analytics.json` 프로젝트에서만 목록·view에 들고, `lab.json` 프로젝트의 데이터셋은 전처럼 `data/raw`·
  `data/derived`입니다. 가져오기 기록은 두 자리에서 다 읽으므로 `lab.json` 프로젝트의 `data/imported/`에 사람이 둔 `*.meta.json`은
  `status`·Workspace에 `unsupported`나 `damaged`로 보입니다. `status`와 Workspace의 CONTEXT 이력은 `.analytics/context/`의 버전도
  보입니다. 읽기 규칙 번호가 2가 되어 `source_revision` 값이 바뀝니다 ([#80]).

같은 번호에 [#13]의 전환 엔진 PR(②)이 다음을 더했습니다.

### Added

- `lab.json` 프로젝트를 새 폴더에 새 형식(`analytics.json`)으로 복사하는 전환 엔진(`aelix_analytics.migration`). 원본에는 옛 kit의 잠금 파일
  둘 말고는 쓰지 않고, 목적지에 만드는 파일은 모두 ledger에 먼저 적은 뒤 없는 이름에만 만들며(이 전환의 디렉터리 안 파일은 교체로 다시 씀),
  목적지 안의 링크를 따라가지 않고, 표식 게시가 마지막 커밋 지점이고, 끊기면 같은 호출로 잇습니다. 아직 명령으로 열지 않았습니다: `analytics migrate`는 다음 PR이 열고, 이 kit의 명령·도구는 그런 목적지에 쓰지 않습니다.
  기록 schema에 `analytics.migration/1`(계획·매핑·보고서)과 표식의 `migration`이 있습니다 ([#13] ②).

### Changed

- 표식 없이 `.analytics/migration/`이나 `.analytics/migration.lock`이 있는 디렉터리(커밋하지 않은 전환의 목적지)에서 kit CLI·SDK의 찾기가
  멈추고 모든 쓰기와 읽기를 새 오류 `migration_incomplete`로 거절합니다. 확장의 작업 디렉터리 찾기도 거기서 멈춥니다. 전에는 그런 디렉터리를
  지나 위로 올라갔습니다 ([#13] ②).
- 전환으로 만든 프로젝트는 옛 kit의 run이 기록한 경로를 그 전환의 매핑(원본 루트의 철자, 복사한 바깥 링크의 대상, `.lab/` → `.analytics/`)을
  거쳐 읽고, `status` JSON에 `project.migration`이 있습니다. 읽기 규칙 번호가 3이 되어 `source_revision` 값이 바뀝니다 ([#13] ②).

기록의 형식과 값은 그대로입니다. 자세한 것은 [현재 계약](docs/CONTRACTS.md) B·D·E·G와 F1의 0.7.0 절에 있습니다.

## [0.6.0] - 2026-10-08

번호를 붙인 PR은 [#13]의 첫 구현 PR(⓪, 내부 이름)인 [#79]입니다.

### Changed

- kit CLI의 결과 JSON에서 Workspace 빌드 결과의 키가 `report`에서 `workspace`로, run의 빌드 실패 키가 `report_error`에서
  `workspace_error`로 바뀝니다. `status` JSON의 Workspace `index.html` 경로도 `workspace`입니다. 값과 그 경로(`report/…`)는 그대로입니다 ([#79]).
- 내부 이름을 Analytics 이름으로 바꿨습니다(기록 형식 그대로, 예외는 아래): `LabError` → `AnalyticsError`, `Project.lab`·`Project.report` → `state_dir`·`workspace_dir`, `report.py` → `workspace.py`, Workspace 자원 `report.js`·`report.css` → `workspace.js`·`workspace.css`(렌더가 옛 파일을 지움), `window.__LAB_BUILD__` → `window.__ANALYTICS_BUILD__`(`build.js`는 0.6.0 전 kit이 만든 페이지를 위해 옛 전역도 정함), 브라우저 저장 키 `lab-live`·`lab-scroll:` → `analytics-live`·`analytics-scroll:`(옛 값을 한 번 읽고 남김), plotly 템플릿 `lab` → `analytics`, `schedule_report` → `schedule_workspace_render` ([#79]).

예외: run 기록의 `finalize_errors` 글처럼 오류 클래스의 이름을 담는 글에는 새 이름(`AnalyticsError`)이 보입니다([현재 계약](docs/CONTRACTS.md) F1).

## [0.5.0] - 2026-10-07

번호를 붙인 PR은 [#68]입니다. [#12]의 셋째 PR([#76])과 [#49]의 수정([#78])은 그 뒤 번호를 올리지 않고 병합됐습니다.

### Changed

- 기록의 경로가 프로젝트 기준 상대 경로 하나로 정해집니다. 프로젝트 안의 디렉터리 링크·junction 아래 파일, 대소문자나 유니코드
  정규화(NFC·NFD)가 다른 철자로 읽은 파일도 같은 경로로 기록되어 입력·출력·데이터셋·생산자 계보가 이어집니다. 철자는 파일 시스템에
  저장된 이름이고, kit이 관리하는 `runs/`·`.venv/`·`.lab/`·`report/`만 저장된 대소문자와 관계없이 그 이름으로 적습니다
  ([#68], [ADR-0006](docs/decisions/0006-common-record-contract.md) 9절).
- 프로젝트 밖을 가리키는 링크는 경계로 보고 그 아래 파일을 링크의 경로로 기록합니다(`external: false`). run이 새로 만든 밖으로의
  링크 아래 파일은 run 시작 뒤에 바뀐 것만 출력입니다 ([#68]).
- 0.5.0 전 kit의 run이 기록한 옛 경로 — 루트나 링크 대상 아래의 실제 절대 경로, 저장된 것과 다른 철자로 기록된 상대 경로(입력
  `data/derived/AGG.parquet`, 차트 `Scripts/Analysis.py::id`) — 를 지금의 경로로 대응해 읽어 데이터셋·생산자·차트 버전과 잇습니다.
  그렇게만 이어진 것은 추론(`basis: inferred`)으로 표시하고 확정 연결로 세지 않으며, 기록은 고쳐 쓰지 않습니다. 0.5.0 이후 run도
  기록한 글자는 옛 run의 것도 기록 그대로 읽습니다 ([#76], [ADR-0006](docs/decisions/0006-common-record-contract.md) 9절).
- status JSON에 추론을 가리키는 필드가 더해집니다: 생산자 판정의 `inferred_candidates`·`read_as`, 참조 판정의 `inferred_candidates`,
  차트 행의 `inferred_versions`·`inferred_inputs`·`inferred_upstream`, 데이터셋의 `inferred`. `analytics_status`·kit `status`와
  Workspace는 그 자리에 `inferred from an older kit's path`를 적습니다 ([#76]).

### Fixed

- 링크한 `data/` 아래에 저장한 출력이 run에서 빠지던 것, junction인 `data/`의 입력이 생산자와 이어지지 않던 것, `runs/`를 가리키는
  junction 아래 run 자신의 파일이 출력으로 기록되던 것, 대소문자만 다른 데이터셋 파일이 kit의 view 이름을 가져가던 것 ([#68]).
- journal의 마지막 줄이 줄 끝(LF) 없이 끊겨 있으면 다음 이벤트를 그 줄에 이어 써서, 그 이벤트가 깨진 줄 안에 들어가고(ADR-0006의
  복구로만 읽힘) 그다음 이벤트가 같은 seq·id를 다시 받던 것. 줄 끝만 없던 온전한 기록이나 CRLF의 CR만 남은 줄 뒤에 쓰면 그 기록을
  읽지 못하게 되던 것. 이제 LF를 먼저 써서 새 이벤트가 따로 한 줄이 되고, seq는 파싱하거나 복구한 모든 줄의 가장 큰 seq 다음
  값입니다(읽히는 줄이 이미 가진 id의 번호는 건너뜀). 이미 있는 바이트는 고쳐 쓰지 않으며 끊긴 줄은 `damaged`로 보입니다
  ([#78], [ADR-0006](docs/decisions/0006-common-record-contract.md) 6절).

0.5.0 전의 기록은 고쳐 쓰지 않습니다. 그 기록의 옛 경로는 위처럼 지금의 경로로 대응해 읽고 추론으로 표시합니다.

## [0.4.0] - 2026-10-06

번호를 붙인 PR은 [#62]입니다. [#64]와 [#66]은 그 뒤 번호를 올리지 않고 병합됐습니다.

### Changed

- kit이 쓰는 텍스트(기록 JSON·JSONL, 템플릿 사본, inline 스크립트, Workspace·차트 HTML, run 로그의 `print`)가 모든 플랫폼에서 LF입니다.
  Windows에서 CRLF였던 기록이 LF가 되어, 같은 내용이면 기록과 inline 스크립트의 `script.sha256`이 플랫폼마다 같습니다. 0.4.0 전에
  Windows에서 쓴 기록은 CRLF 그대로 읽고 고쳐 쓰지 않습니다 ([#62], [ADR-0007](docs/decisions/0007-line-endings.md)).
- 새 프로젝트에 `.gitattributes`(`* -text`)가 생깁니다. 줄 끝만 바뀐 입력과 코드에는 사유 `line_endings_only`가 붙고, 줄 끝만 바뀐
  CONTEXT 버전은 `CONTEXT.md line endings changed`로 보입니다 ([#62]).
- kit이 보이는 글이 영어입니다: Workspace의 탭(Overview·Visualizations·Timeline·Data·Runs·Context·Evaluation)·배지·버튼·빈 화면·오류와
  status 안내. 사람과 스크립트가 쓴 글은 번역하거나 정규화하지 않고 쓴 그대로 보이며, 데이터셋은 SQL view 이름이 아니라 쓴
  그대로의 이름으로 보입니다 ([#66]).
- 새 프로젝트의 `CONTEXT.md` 템플릿(고정 표식 `<!-- pin -->`)과 `analytics demo`가 영어이고, 새 프로젝트의 uv 프로젝트 이름은
  `analytics-<slug>`입니다. 기존 프로젝트의 파일은 바꾸지 않고 옛 템플릿의 제목과 `<!-- 항상 -->`도 읽습니다 ([#66]).

### Fixed

- 결과에 U+2028·U+2029·U+0085가 든 명령이 끝났는데도(run은 기록됐는데도) 확장에 `kit_failed … without a result`로 보이던 것.
  CLI 결과 줄이 세 문자를 이스케이프하고, 확장은 LF에서만 줄을 나눕니다 ([#64]).

## [0.3.0] - 2026-10-06

번호를 붙인 PR은 [#60]입니다. [#61]은 그 뒤 번호를 올리지 않고 병합됐습니다.

### Changed

- 공개 이름이 `analytics`입니다. 별칭은 없습니다: 사람용 명령 `bin/lab` → `bin/analytics`, 하위 명령 `note`·`report` →
  `record`·`render`, aelix 명령 `/lab` → `/analytics`, 도구 `lab_*` → `analytics_*`(`lab_note` → `analytics_record`), flag
  `--lab-project` → `--analytics-project`, 확장 파일 `extension/aelix_lab.py` → `extension/aelix_analytics.py`, SDK `note`·`chart` →
  `record`·`visualize` ([#60]).
- 환경 변수가 `AELIX_ANALYTICS_*`입니다(`LAB_PROJECT`·`AELIX_LAB_PROJECT` → `AELIX_ANALYTICS_PROJECT`, `AELIX_LAB_KIT` →
  `AELIX_ANALYTICS_RUNTIME_PATH` 등). 옛 이름을 설정해 두면 그것을 읽던 쪽이 새 이름을 알리는 오류로 멈춥니다. 기록의 형식과 저장
  이름(`lab.json`, `.lab/`, `lab.*/1`)은 그대로입니다. 옮기는 방법은 [README](README.md#옛-이름에서-옮기기)에 있습니다 ([#60]).
- status와 Workspace가 기록을 한 읽기 모델로 읽어 같은 판정을 보입니다: 읽기 기준(`as_of_seq`·`source_revision`), 마지막 run 대신
  같은 바이트를 출력한 생산자 후보, 읽지 못한 기록과 손상된 줄의 위치, run의 기록 상태(`recording incomplete`) ([#61]).
- status JSON: `datasets[].producer`는 지금 바이트를 출력한 성공 run이 하나일 때만 있습니다. 위치를 주는 필드(`unsupported`·
  `damaged`·`not_listed`·`shared_ids`, `datasets[].producer_candidates` 등)가 더해졌습니다 ([#61]).

### Removed

- status JSON의 `unreadable`(가족·버전별 개수). 위의 위치 필드가 대신합니다 ([#61]).

## [0.2.0] - 2026-10-06

번호를 붙인 PR은 [#58]입니다. [#59]는 그 뒤 병합됐고 보이는 동작을 바꾸지 않았습니다.

### Changed

- kit이 package `aelix_analytics`가 되었습니다. 스크립트의 SDK는 `import aelix_analytics as analytics`이고, `labkit`을 import하는
  스크립트는 안내와 함께 실패합니다(별칭 없음). 체크아웃의 실행 루트가 `<pack>/kit`에서 `<pack>/src`로 바뀌었습니다 ([#58]).
- 설치된 배포본의 package를 `python -m aelix_analytics`로 부르면 거절합니다. 설치 환경에서는 `analytics`를 씁니다 ([#58]).
- 도움말·안내·오류의 SDK 호출 예가 `analytics.read(…)` 형태입니다. 기록의 `via` 값은 그대로입니다 ([#58]).

## [0.1.0] - 2026-09-16

첫 kit은 PR 없이 main에 들어갔습니다([`6dc010b`](https://github.com/handochan/aelix-analytics/commit/6dc010bada0823a60064f233f3b930a666d52682)).
그 뒤 PR 없이 들어간 commit([`fb06e8a`]·[`7b205cd`], 이름을 Aelix Analytics로 바꾼 [`21b6a3c`]까지)과, 번호를 올리지 않고 병합된 아래의
PR도 이 절입니다.

### Added

- 분석 프로젝트: `CONTEXT.md`와 그 버전, 시간순 기록(journal), run 기록(실행한 코드의 바이트, 입력·출력과 sha256, 차트와 그 데이터,
  환경), 프로젝트마다의 uv 환경, 차트에서 코드·데이터로 가는 정적 HTML 리포트. 사람용 `bin/lab`과 aelix 확장(`lab_*` 도구 여섯 개,
  `/lab`).
- 모르는 버전의 기록을 읽지 않고 그 개수와 schema를 status와 리포트에 보입니다. 더 새 버전의 `lab.json`은 `project_too_new`로
  거절합니다. pandas 습관이나 옮겨 간 Polars API로 실패한 run에는 대신 쓸 표현을 `error.hint`로 남깁니다 ([`fb06e8a`]).
- `CONTEXT.md`를 `##` 섹션 단위로 맥락에 넣습니다. `<!-- 항상 -->`을 붙인 섹션과 앞에서부터 예산 안에 드는 섹션은 본문째,
  나머지는 제목과 줄 범위만 넣습니다 ([`7b205cd`]).
- Linux·macOS·Windows를 위한 이식 가능한 경계: 잠금, 기록 교체, 자식 프로세스, run의 제한 시간과 취소 ([#31]).
- 배포본: wheel 하나, 모든 플랫폼의 사람용 `analytics`(Windows에서는 `analytics.exe`), aelix가 entry point로 찾는 설치본 확장 ([#33],
  [ADR-0004](docs/decisions/0004-minimal-distribution-and-entry-points.md)).
- 확장이 모델에 보내는 글에서 생략하거나 자른 곳마다 빠진 양과 다시 읽을 위치를 적습니다 ([#53]).

### Changed

- 이식 가능한 경계와 배포본을 더하며 POSIX에서도 바뀐 동작: 예약 장치명과 플랫폼마다 다르게 읽히는 스크립트 경로의 거절, 리포트의
  `file://` 링크, run 중단의 기록, UTF-8로 고정한 CLI 입출력 등. 항목별로는
  [현재 계약 F1](docs/CONTRACTS.md#f1-이전-구현48b79cc과-달라진-posix-동작)에 있습니다 ([#31], [#33]).

### Fixed

- Windows에서 `taskkill`을 이름이 아니라 시스템 디렉터리의 절대 경로로 실행합니다. 작업 디렉터리의 같은 이름 프로그램을 실행하지
  않습니다 ([#35]).
- Windows에서 이미 끝난 프로세스 때문에 프로세스 트리가 남았다고 잘못 알리던 것 ([#57]).

[Unreleased]: https://github.com/handochan/aelix-analytics/commits/main
[0.4.0]: https://github.com/handochan/aelix-analytics/commit/402c78a42dc9b6a6f660f3a43d4f5c6e2a0fd9a7
[0.3.0]: https://github.com/handochan/aelix-analytics/commit/558efff58868967a38bd38de0920deff989b35fc
[0.2.0]: https://github.com/handochan/aelix-analytics/commit/938d75449b9b2463a91a3bf1b84169ce3618847f
[0.1.0]: https://github.com/handochan/aelix-analytics/commit/6dc010bada0823a60064f233f3b930a666d52682
[`fb06e8a`]: https://github.com/handochan/aelix-analytics/commit/fb06e8ac4555c1769cd17d401a0180247b3dbcb3
[`7b205cd`]: https://github.com/handochan/aelix-analytics/commit/7b205cd16738bc11de1d23e538c9423e7a55ea27
[`21b6a3c`]: https://github.com/handochan/aelix-analytics/commit/21b6a3c5d98e688aa3642662c4eb4b9093a12247
[#12]: https://github.com/handochan/aelix-analytics/issues/12
[#13]: https://github.com/handochan/aelix-analytics/issues/13
[#49]: https://github.com/handochan/aelix-analytics/issues/49
[#31]: https://github.com/handochan/aelix-analytics/pull/31
[#33]: https://github.com/handochan/aelix-analytics/pull/33
[#35]: https://github.com/handochan/aelix-analytics/pull/35
[#53]: https://github.com/handochan/aelix-analytics/pull/53
[#57]: https://github.com/handochan/aelix-analytics/pull/57
[#58]: https://github.com/handochan/aelix-analytics/pull/58
[#59]: https://github.com/handochan/aelix-analytics/pull/59
[#60]: https://github.com/handochan/aelix-analytics/pull/60
[#61]: https://github.com/handochan/aelix-analytics/pull/61
[#62]: https://github.com/handochan/aelix-analytics/pull/62
[#64]: https://github.com/handochan/aelix-analytics/pull/64
[#66]: https://github.com/handochan/aelix-analytics/pull/66
[#68]: https://github.com/handochan/aelix-analytics/pull/68
[#76]: https://github.com/handochan/aelix-analytics/pull/76
[#78]: https://github.com/handochan/aelix-analytics/pull/78
[#79]: https://github.com/handochan/aelix-analytics/pull/79
[#80]: https://github.com/handochan/aelix-analytics/pull/80
