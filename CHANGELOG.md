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

## [0.12.0] - 2026-10-10

- 사람의 CLI `assets mode`와 SDK `set_asset_mode`로 프로젝트의 요청 시/상시 제안 모드를 선택합니다. 기본 모드는 on_demand이며 모델을 호출하지 않습니다.
- 일반 사람 텍스트 status에서 확정 근거·점수·거절 사유·명시적 정체성·롤링 창을 잠금 안에서 확인하고 자동 알림 하나를 먼저 기록합니다. 출력 실패도 창을 소비하며 미응답 기록은 pending으로 조회합니다.
- JSON·에이전트·SDK status와 모든 records/relations/context 상세 페이지는 자동 알림을 발행하지 않습니다. Aelix 세션·맥락 발행·Workspace 자산 화면과 Q11은 다음 B3-c 범위입니다.

## [0.11.0] - 2026-10-10

번호를 붙인 변경은 [#15 구현 PR89](https://github.com/handochan/aelix-analytics/pull/89)입니다.

- 기존 analytics_status와 CLI·SDK에서 같은 기준으로 기록 검색·전체 필드·맥락 섹션·한 단계 근거를 페이지로 읽습니다.
- 한국어·코드 어휘, 과거 판단·정정 연결, source revision/cursor와 읽는 동안의 변경을 표시합니다. 파생 인덱스가 없어도 원본으로 답합니다.

## [0.10.0] - 2026-10-10

번호를 붙인 변경은 [#17 B3-a 구현 PR88](https://github.com/handochan/aelix-analytics/pull/88)입니다.

- 분석 자산(Analysis asset)의 후보·검사·효과를 CLI·SDK·기존 analytics_record 도구에서 기록하고, 정확한 산출물 버전·독립 입력·경계 검사로 승격합니다.
- 명시적 프로젝트 등록은 원본 후보 바이트를 보존하고 목적지에 복사하며, 게시된 작업 receipt가 있는 불완전 등록만 이어서 기록합니다. 같은 내용의 기존 파일을 이 작업의 결과로 채택하지 않습니다.
- 요청한 후보 검토는 모델 호출 없이 판정하고 requested 이력을 남깁니다. 자동 제안·모드·Aelix 세션/맥락 발행은 #17의 다음 구현에서 다룹니다.
- 새 이름은 Asset candidate / Asset suggestion입니다. 과거 reuse_* 기록·논리 키·참조는 그대로 읽습니다. 등록·실제 loader 발견·실행은 각각의 상태로 보입니다.

## [0.9.0] - 2026-10-10

- CLI·SDK·기존 analytics_record 도구의 공통 경로로 미정/구체화 지표 정의, 정확한 부모·수정 버전, 현재 분석 초점과 명시적인 run 관측을 기록합니다.
- run 시작의 초점과 과거 정의·관측 범위를 보존하며 실행 상태·기록 완료·미검증 계산을 구별합니다. 자유 Python 분석에는 지표 등록이 필요하지 않습니다.
- 구조화 details가 상태 변경 관계를 정하고 추가 caller 상태 변경은 거절합니다. 잠금 안에서 동시 수정을 다시 검사하며 과거 비구조화 기록을 추정으로 바꾸지 않습니다.

## [0.8.0] - 2026-10-09

번호를 붙인 PR은 [#13]의 셋째 구현 PR(③, 새 writer와 정책 전환)인 [#84]입니다. 기록 형식이 바뀌는 버전입니다:
이 kit은 새 형식(`analytics.json`)의 프로젝트에만 쓰고, 그 전의 `lab.json` 프로젝트는 읽기만 하며 `analytics migrate`로 새 폴더에 옮깁니다.
바뀐 저장 이름의 표는 [README](README.md#옛-이름에서-옮기기)에 있습니다.

### Added

- `analytics migrate --to DIR [--dry-run]`(설치본 `analytics`, `bin/analytics`; kit의 `migrate`): `lab.json` 프로젝트를 새 빈 폴더에 새 형식으로
  복사합니다. 원본에는 옛 kit의 잠금 파일 둘 말고는 쓰지 않고, `--dry-run`은 아무것도 쓰지 않고 계획과 거절 이유를 먼저 보이며, 끊긴 전환은 같은
  명령으로 잇고 끝난 전환을 다시 부르면 원본과 사본을 대조합니다. 두 런처는 이 명령을 프로젝트의 환경 없이(`uv run --no-project`) 부르고, 커밋한
  뒤 kit이 답한 목적지(`~` 포함, kit이 푼 경로)의 환경을 `uv sync --locked`로 준비합니다(실패해도 전환은 끝났다고 알리고 0). 에이전트가 부른
  전환은 보고서와 응답에 `agent`로 남습니다.
  aelix의 도구·`/analytics`에는 이 명령이 없습니다. `.venv/`·`report/`·캐시·잠금과 운영체제 메타 파일(`.DS_Store`·`Thumbs.db`·`desktop.ini`,
  macOS가 FAT·exFAT 같은 볼륨에 만드는 AppleDouble `._<이름>`; AppleDouble은 2026-10-09 소유자 결정이며 일반 파일만, 그런 이름의 폴더·링크는
  보통 항목으로 복사)은 복사하지 않고 보고서에 적으며, 목적지의 것은 무시합니다(복사가 원본의 폴더·링크를 놓을 경로의 것은 쓰기 전에
  `not_empty`로 거절). 첫 ledger 줄이 끊긴 전환 디렉터리 옆에 macOS가 남긴
  `._progress.jsonl`도 내용으로 보지 않아 다시 부르면 새로 시작하고, 그 디렉터리를 지우지 못하면 `destination_write_failed`로 지울 폴더를
  알립니다. 그래서 macOS에서도 FAT32 같은 볼륨으로 전환할 수 있습니다 ([#84]).
- 링크를 만들 수 없는 목적지(FAT·exFAT 등)에서는 전환이 표식을 제자리에 쓰지 않고 이미 있는 이름을 바꾸지 않는 rename(Linux
  `renameat2(RENAME_NOREPLACE)`, macOS `renamex_np(RENAME_EXCL)`, Windows `os.rename`; 포트의 `AtomicFiles.rename_new`)으로 게시합니다. 그것도
  없는 목적지는 아무것도 복사하기 전에 `destination_invalid`(`details.reason: no_exclusive_rename`)로 거절합니다 ([#84]).
- aelix 확장이 aelix 프로세스의 환경에 `AELIX_ANALYTICS_ACTOR=agent`와 그 세션을 넣으므로, 에이전트가 aelix의 bash 도구로 부른 `analytics`·
  `python -m aelix_analytics`가 `agent`로 기록됩니다. 사람이 aelix에서 `!analytics …`처럼 친 줄이 aelix가 그 줄을 돌릴 셸(POSIX 셸이나
  PowerShell)로 읽어 전체로 kit 명령 하나이면 확장의 `user_bash` 처리가 `human`으로 기록하게 하고, 다른 `!` 줄 — kit을 부르지 않거나,
  `;`·`&&`·`|`·줄바꿈·`$(…)` 등으로 다른 명령과 합친 줄, 셸마다 읽는 법이 다른 `$`·`env -S`·모르는 옵션이 든 줄, cmd·fish 같은 다른 셸의 줄,
  Windows에서 cmd가 다시 읽을 수 있는 글자(`&`·`|`·`%`·`"` 등)를 담은 단어가 있는 줄, 셸이 펼치는 패턴·중괄호(`*`·`{a,b}`)나 PowerShell이
  단어를 달리 나누는 것(`'a'b`·`-p3.12`·`a,b`·`@x`)이 든 줄 — 은 건드리지 않습니다(그 안의 kit 기록은 `agent`). 한계: aelix 0.1.0b2는 마지막 확장의 답만 쓰므로, `!` 명령을 막거나 다른 실행기로 돌리는 확장이 먼저
  등록되면 kit 명령 하나인 줄에 한해 이 처리가 그 막음·실행기를 덮습니다(소유자가 2026-10-09에 그 범위로 정함, [현재 계약](docs/CONTRACTS.md)
  A). aelix 밖의 에이전트 호스트는 `AELIX_ANALYTICS_ACTOR=agent`를 설정합니다 ([#84]).

### Changed

- 쓰는 형식이 `analytics.project/2`입니다. `lab.json` 프로젝트의 모든 쓰기(run·import·record·render·demo, SDK, aelix 도구와 `/analytics`)는 새
  오류 `migration_required`와 전환하는 법을 알리는 안내로 첫 부수 효과 전에 거절되고, 맥락 동기화·캐시·로그 같은 부수 쓰기는 건너뜁니다. `status`
  JSON의 `project.migration_hint`와 글·맥락 블록의 `Older record format: …` 줄이 그것을 알립니다. `analytics open`은 있는 `report/index.html`을
  열고 더 갱신되지 않는다고 적으며, 페이지가 없으면 만들지 않고 그 사실과 전환하는 법을 적고 1로 끝납니다. 위쪽 디렉터리에 `lab.json`이 있는 새 형식 프로젝트는 `project.older_project_above`와 `warning:` 줄로
  알립니다 ([#84]).
- 새 프로젝트(`analytics init`·`demo`, `/analytics init`·`demo`, `analytics_init`)는 새 저장 이름입니다: `analytics.json`(`project_id`,
  `min_writer_version`·`kit_version` 0.8.0), `.analytics/`, `workspace/`, `data/imported/`, `runs/<id>/visualizations/`, 새 `.gitignore`.
  다른 프로젝트 안의 디렉터리와 끝나지 않은 전환의 목적지(표식 없이 전환 기록만 있는 폴더) 안에는 만들지 않습니다(`destination_invalid`,
  `details.reason: inside_project`; 전환 목적지는 2026-10-09 소유자 결정이며 `analytics migrate --to`도 같음). 그 전환은 같은 명령으로 이어
  끝냅니다. `.DS_Store`와 AppleDouble `._<이름>` 파일만 있는 폴더는 빈 폴더로 봅니다(그런 이름의 폴더·링크는 항목) ([#84]).
- 기록: journal 줄은 `analytics.event/2`(`actor_basis`·`means`·`request_id`, 쓰는 때 결박한 참조의 `version_binding`·`relation`·`basis`·`digest`),
  run은 `analytics.run/2`(`visualizations[]`·`sha256`·`data_sha256`, `reproduction.visualizations`), 가져오기는 `data/imported/`의
  `analytics.dataset/1`, 입출력의 `via`는 `analytics.*`, 맥락 버전은 `.analytics/context/`이고 `context` 이벤트가 sha256으로 가리킵니다.
  옛 형식의 기록은 그대로 읽습니다 ([#84]).
- 기록의 본문을 자르지 않습니다. 32 KiB(UTF-8)를 넘거나 줄(LF 포함)이 50 KiB 미만이 아니면 새 오류 `text_too_long`이고 아무것도 쓰지
  않습니다 — `record`·`analytics_record`가 먼저 하던 맥락 동기화도 하지 않고, run 안의 `analytics.record`는 부를 때 예외입니다(전에는 모든
  진입점이 4,000자 뒤를 버렸습니다). kit이 만드는 기록(가져오기·run 완료·프로젝트 생성)은 다른 곳에 통째로 남는 설명·제목을 4,000자까지
  인용합니다. 이벤트당 참조 24개의 상한과 풀지 못한 이름의 `other[]`는 없어졌습니다 ([#84]).
- kind `gap`은 `workflow_feedback`이 되었습니다. 입력의 `gap`은 `bad_kind`(안내가 새 이름을 알림)이고, kit CLI `record`의 모르는 kind는 사용법
  오류 2가 아니라 `bad_kind`와 1입니다. 옛 기록의 `gap`은 그대로 읽습니다 ([#84]).
- 쓰기(`record`·`run`·`import`·`init`·`render`·`demo`·`migrate`, `analytics_init`)의 응답에 `request_id`가 있고, `record`는 결박(`bindings`)과
  상태 변화(`targets`: `state_before`·`state_after`, 이미 닫힌 대상이면 앞의 기록 `made_by`)를 돌려줍니다. 공통 writer의 상태 변경(`record_update`)은
  같은 상태 변경이 겹치면 `state_conflict`로 거절하며, 그것을 부르는 공개 입력은 #14·#19가 엽니다 ([#84]).
- 작성 출처: 기록에 `actor_basis`(`declared`·`inferred`)와 `means`(`cli`·`sdk`·`tool`·`command`·`hook`)가 있습니다. `analytics`·
  `bin/analytics`는 `AELIX_ANALYTICS_ACTOR=human`을 채워 넘기지 않아 사람이 부른 기록은 `human`·`inferred`이고, run 밖 SDK의 작성 주체는
  `script`가 아니라 `human`(또는 밝힌 값)과 `sdk`입니다. kit CLI는 확장이 넘기는 내부 변수 `AELIX_ANALYTICS_MEANS`를 읽고 닫힌 목록 밖의 값은 새
  오류 `bad_means`로 거절합니다 ([#84]).
- 신뢰 목록이 `analytics-projects.json`(`analytics.trust/1`)입니다. 옛 `lab-projects.json`은 고치지 않고 `lab.json` 프로젝트의 활성화에만
  읽습니다 ([#84]).
- 관리 디렉터리가 표식의 배치마다입니다: 새 프로젝트의 `.lab/`·`report/`는 사람의 폴더라 run이 거기 쓴 파일은 출력이고, 새 프로젝트의
  `workspace/`·`.analytics/`는 출력이 아닙니다 ([#84]).
- `analytics`·`bin/analytics`의 프로젝트 찾기가 표식 없는 전환 목적지에서 멈추고 그 자리의 명령을 환경 없이 kit에 넘겨 `migration_incomplete`를
  보입니다(전에는 지나 위로 올라갔고, uv가 그 자리에 `.venv/`를 만들 수 있었습니다). aelix 확장도 명시한 프로젝트가 그런 목적지이면
  `migration_incomplete`입니다 ([#84]).

### Removed

- 공개 계약이 아닌 내부 이름 `core.add_note`·`append_event`·`NOTE_KINDS`·`MAX_EVENT_TEXT`·`MAX_EVENT_REFS`·`resolve_ref_aliases`와
  `/1` 쓰기 경로. 기록은 `core.record`·`writer.Writer`로 씁니다. 저장소 도구 `tools/eval_fixtures.py`의 `--format v1`은 `--kit-root`로 준
  0.8.0 전 kit으로만 만듭니다 ([#84]).

자세한 것은 [현재 계약](docs/CONTRACTS.md) A–G와 F1의 0.8.0 절, [측정 기록](docs/verification/2026-10-09-issue-13-writer.md)에 있습니다.

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

같은 번호에 [#13]의 전환 엔진 PR(②, [#82])이 다음을 더했습니다.

### Added

- `lab.json` 프로젝트를 새 폴더에 새 형식(`analytics.json`)으로 복사하는 전환 엔진(`aelix_analytics.migration`). 원본에는 옛 kit의 잠금 파일
  둘 말고는 쓰지 않고, 목적지에 만드는 파일은 모두 ledger에 먼저 적은 뒤 없는 이름에만 만들며(이 전환의 디렉터리 안 파일은 교체로 다시 씀),
  목적지 안의 링크를 따라가지 않고, 표식 게시가 마지막 커밋 지점이고, 끊기면 같은 호출로 잇습니다. 아직 명령으로 열지 않았습니다: `analytics migrate`는 다음 PR이 열고, 이 kit의 명령·도구는 그런 목적지에 쓰지 않습니다.
  기록 schema에 `analytics.migration/1`(계획·매핑·보고서)과 표식의 `migration`이 있습니다 ([#82]).

### Changed

- 표식 없이 `.analytics/migration/`이나 `.analytics/migration.lock`이 있는 디렉터리(커밋하지 않은 전환의 목적지)에서 kit CLI·SDK의 찾기가
  멈추고 모든 쓰기와 읽기를 새 오류 `migration_incomplete`로 거절합니다. 확장의 작업 디렉터리 찾기도 거기서 멈춥니다. 전에는 그런 디렉터리를
  지나 위로 올라갔습니다 ([#82]).
- 전환으로 만든 프로젝트는 옛 kit의 run이 기록한 경로를 그 전환의 매핑(원본 루트의 철자, 복사한 바깥 링크의 대상, `.lab/` → `.analytics/`)을
  거쳐 읽고, `status` JSON에 `project.migration`이 있습니다. 읽기 규칙 번호가 3이 되어 `source_revision` 값이 바뀝니다 ([#82]).

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
[#82]: https://github.com/handochan/aelix-analytics/pull/82
[#84]: https://github.com/handochan/aelix-analytics/pull/84
