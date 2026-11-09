# ADR-0015: kit 밖 실행의 관찰 기록 — `observed_activity`

Date: 2026-11-09

Status: Proposed — [#171](https://github.com/handochan/aelix-analytics/issues/171)의 결정 PR이다. 소유자 결정(2026-11-09, #171의 권장안 (b),
[첫 출시 0.32.1의 범위](https://github.com/handochan/aelix-analytics/issues/24#issuecomment-6742451632))의 기록 형식을 구체화한다.
새 기록 종류를 더하는 결정이므로 이 PR의 리뷰가 [리뷰 예산](../PROJECT-OPERATIONS.md#리뷰-예산)의 설계 리뷰(독립 1회·Codex 1회)다.
채택은 구현 완료가 아니다. 이 PR은 runtime·schema·kit 버전·표식을 바꾸지 않는다.

## 맥락과 현재 코드

기준은 main `6fd956c`(kit 0.32.1)이다.

- 확장은 모든 도구 호출을 `.analytics/telemetry.jsonl`에 한 줄로 남긴다(`extension/aelix_analytics.py`의 `on_tool_end`·`summarize_call`, 1728행).
  bash는 명령 앞 300자(가리지 않음)와 `python_via_bash` 표시, write·edit는 경로와 `wrote_managed_path`다. 이 로그는 사용량을 재는 보조 로그다
  ([계약 C5](../CONTRACTS.md#c5-보조-로그)). journal seq가 없고, 공통 reader·출처 추적·Timeline은 읽지 않는다. Workspace Evaluation의
  `Possible workarounds`와 `analytics_status`의 한 줄 안내(`bypass_note`, 795행)에만 나온다.
- 그래서 bash로 돌린 Python이 `data/derived/x.parquet`를 쓰면 그 파일은 데이터셋 목록에 있지만 생산자가 없다([C18](../CONTRACTS.md#c18-출처-추적-kit-0240-100)의
  `producer_state=none`). 그 수치를 인용한 발견도 근거 없이 남는다. #170의 알림은 그 자리에서 모델에게 알릴 뿐 흔적을 남기지 않는다.
- run은 실행 전후로 프로젝트 이름 공간의 `{경로: (크기, mtime_ns)}`를 비교하고(`runner.tree_stat`, 129행) 바뀐 파일만 해시한다
  (`runner.diff_outputs`, 179행; `core.HashCache`, 1184행). 코드 스냅샷·입력(읽은 시점의 sha256)·환경(`env`)은 run 안의 capture hook이 남긴다.
  kit 밖의 실행에는 그 hook이 없다.
- aelix 0.1.0b2: `tool_call` hook은 각 도구 실행 전에 기다려 부르며 막을 수 있다. `tool_result` hook은 실행 뒤에 결과를 덧붙이거나 바꾼다
  (`ToolResultPatch`). `tool_execution_start`·`_end`는 관찰용이다. `bash`·`write`·`edit` 도구는 `execution_mode="sequential"`이라 그것이 든
  한 묶음의 도구 호출은 차례로 돈다(`aelix_agent_core/loop.py`). 사람의 `!` 명령에는 실행 전 `user_bash` 처리기만 있고 실행 뒤 hook이 없다.
- #170(진행 중)은 같은 `tool_result` hook으로 `python_via_bash`인 bash 결과 끝에 "기록되지 않았다"는 안내를 붙인다. 막지 않는다.
- 확장은 aelix 프로세스 안에서 stdlib 전용 kit 모듈 넷(`core`·`_platform`·`relations`·`writer`)만 import한다([계약 A](../CONTRACTS.md#a-aelix-확장-표면--쓰는-것-전부)).
  runner는 import하지 않는다.
- 공유본([D3](../CONTRACTS.md#d3-사적-공유-build와-완료-결박-kit-0260-20-pr3)·D4)은 허용 목록 투영이다. `publishing/scope.py`의 `PROJECT_KINDS`에 없는 kind는
  두 프로필 모두 빠진다.
- 측정(이 PR의 probe, 소스 `6fd956c`): Apple M5 Pro·macOS·APFS·warm cache에서 `Project.walk()`와 파일마다 `os.stat`이 파일 1천 개 약 5 ms,
  1만 개 약 50 ms, 5만 개 250–350 ms였다. Linux·Windows·cold cache·네트워크 드라이브는 재지 않았다.

## 결정

### 1. 관찰된 활동이란 무엇인가

관찰된 활동(observed activity)은 **활성 프로젝트에서 모델이 aelix `bash` 도구로 실행했고 kit이 run으로 기록하지 않은 명령 하나를,
확장이 그 도구 호출의 앞뒤에서 본 기록**이다. 남기는 것은 kit이 직접 본 사실 — 명령 글, 시각, 앞뒤의 파일 상태, 명령이 가리킨 스크립트의
바이트 — 뿐이다. 그 명령이 무엇을 읽었고 어떤 환경에서 무엇을 했는지는 남기지 않는다. run이 아니다.

다음을 모두 만족할 때 하나를 기록한다.

1. 활성 프로젝트가 있고 이 kit이 쓰는 프로젝트다. 판정은 telemetry와 같은 부수 쓰기 규칙(`admit(..., side=True)`)이다. 읽기 전용·옛 형식·
   전환 미완료·표식 충돌이면 아무것도 쓰지 않는다.
2. 도구가 aelix의 `bash`이고 그 호출이 실제로 실행됐다(`tool_result`가 왔다). 다른 확장의 `tool_call`이 막은 호출은 기록하지 않는다.
3. 다음 가운데 하나가 참이다.
   - **분석 실행**: 지금의 `python_via_bash` 판정(`PYTHON_IN_SHELL`이고 `KIT_IN_SHELL`·`bin/analytics`가 아님)이 참이고, aelix의 작업
     디렉터리가 프로젝트 안이거나 명령이 프로젝트 안의 파일을 가리킨다(결정 4).
   - **설명되지 않은 변경**: 결정 3의 관찰 범위에서 바뀐 파일이 있고, 그 가운데 하나라도 같은 창에 kit이 기록한 run·가져오기로 설명되지 않는다.

둘 다 아니면(`ls`, `git status`, `analytics run …`만 한 호출) 기록하지 않는다. 줄 전체가 kit 명령 하나인 호출(`user_bash`와 같은 판정)은
앞뒤 상태도 보지 않는다.

v1의 범위 밖: write·edit 도구의 직접 쓰기(telemetry의 `wrote_managed_path`는 그대로), 사람의 `!`·`!!` 명령(실행 뒤 hook이 없고, 관찰하려면
사람의 셸을 감싸야 해서 A의 `user_bash` 범위 결정과 충돌), 다른 확장·MCP 도구, aelix 밖 에이전트 호스트의 셸, 명령이 남긴 백그라운드
프로세스가 도구 호출 뒤에 한 일. 앞의 둘은 OQ2다.

### 2. 저장 형식: `analytics.event/2`의 새 kind `observed_activity`

- 새 기록 가족을 만들지 않는다. [ADR-0006](0006-common-record-contract.md) §1의 2항이 #13 뒤의 기능에 허용한 새 kind로 `observed_activity`를 더한다.
  공통 필드(`schema`·`seq`·`id`·`recorded_at`·`kind`·`actor`·`text`·`refs`)의 뜻은 그대로다.
- 작성 출처(`Origin`)는 기록을 쓴 쪽이다: `actor=system`, `actor_basis=declared`, `means=hook`, `session`은 그 aelix 세션. 명령을 실행한 쪽은
  `details.performer`에 따로 둔다. 관찰은 에이전트가 쓴 기록이 아니므로 그 수에 섞지 않는다.
- `text`는 kit이 만드는 영어 한 줄이다: `Observed outside the kit: bash "<가린 명령 앞 160자>" — N files changed`. 사용자 글이 아니다.
- `refs`는 빈 목록이다. 입력을 모르므로 `uses`·`derived_from`을 만들지 않는다. 다른 기록은 이 관찰을 기존 `event` 참조(`e<seq>`)로
  인용할 수 있다. 새 참조 type은 없다.
- `activity_id`는 `act-` 뒤에 소문자 Crockford base32 ULID 26자다(`^act-[0-9a-hjkmnp-tv-z]{26}$`, `pub-`·`att-`와 같은 규칙). 모든 관찰에 있다.
  순서는 seq이고 ID의 시각 성분이 아니다. 재시도 키가 아니며 `request_id`도 쓰지 않는다.
- 첨부(가린 명령 전문, 긴 변경 목록, 스크립트 사본)가 있으면 `.analytics/observed/<activity_id>/`를 배타적으로 만들어 파일을 쓰고, 그다음
  journal 잠금 안에서 이벤트를 덧붙인다. **이벤트가 커밋 지점이다.** 이벤트 없는 폴더는 `recording_status=incomplete`
  (`no_completion_event`)이고 개수·출처 추적에 쓰지 않는다. 이벤트 전에 실패하면 자기 폴더의 삭제를 시도하고, 다른 폴더는 자동으로 정리하지 않는다.
- 줄 상한(50 KiB, [C1](../CONTRACTS.md#c1-이벤트-journaljsonl))을 넘지 않도록 아래 인라인 상한을 직렬화한 바이트로 잰다. 넘는 부분은 첨부로
  보내고 그 sha256을 이벤트에 둔다. 관찰의 실패는 도구 결과의 내용·오류 여부를 바꾸지 않는다(#170의 안내 줄은 결정 11).

새 writer가 쓰는 `details`는 다음과 같다. 필드는 모두 필수이며 모르는 값은 null이다.

| 필드 | 계약 |
|---|---|
| `contract` | `observed_activity/1` |
| `activity_id` | 위의 ID |
| `performer` | `{actor: "agent", tool: "bash", tool_call_id}`. `tool_call_id`는 aelix가 준 불투명 값이며 세션 기록에서 출력을 찾는 데 쓴다 |
| `started_at`, `ended_at`, `duration_ms` | 앞 상태를 보기 직전과 뒤 상태를 본 직후의 시각(offset 포함). 명령 자체의 시작·끝이 아니라 관찰 창이다 |
| `cwd`, `cwd_outside` | aelix 작업 디렉터리의 프로젝트 상대 POSIX 경로(루트는 `.`)와 `false`. 밖이면 `null`과 `true`. 절대 경로는 남기지 않는다 |
| `command` | 결정 5로 가린 명령: `{text, bytes, sha256, inline_complete, truncated, redaction: {rules_version, count}}`. `text`는 직렬화해 8 KiB 이하인 앞부분, 전문은 첨부 `command.txt`. `bytes`·`sha256`은 **가린** 전문의 것 |
| `result` | `{is_error, exit_code}`. aelix가 준 것만이고 모르면 null. 출력 내용은 남기지 않는다 |
| `trigger` | 참인 조건의 목록: `analysis_command`, `unexplained_changes` |
| `window` | `{seq_before, seq_after}`: 창의 시작과 끝에 본 journal의 마지막 seq |
| `changes` | 결정 3 |
| `scripts`, `mentioned` | 결정 4 |
| `captures` | 첨부마다 `{path, role: command·changes·script, bytes, sha256}`. `path`는 `.analytics/observed/<activity_id>/` 기준. 없으면 빈 목록 |

### 3. 앞뒤의 파일 상태

- **범위**는 run의 출력 비교 대상과 같은 이름 공간이다([계약 B](../CONTRACTS.md#b-프로젝트-파일-규약): `.venv .analytics runs workspace scripts .git
  __pycache__ …`와 프로젝트 자신의 파일을 뺀 모든 파일, 밖을 가리키는 링크 경계 포함). 같은 `Project.walk()`와 같은 경로 정체성(E)을 쓰므로
  관찰의 경로와 run의 입력·출력 경로가 같은 키다. 대부분은 `data/`이고 `outputs/` 같은 사용자 폴더도 든다. `scripts/`는 코드라 빠진다
  (명령이 가리킨 스크립트는 결정 4). 관리 영역(`runs/`·`.analytics/`·`workspace/`·journal·표식)의 손 변경을 감지한다고 말하지 않는다.
- **방법**: `tool_call` hook에서 `{정체성 경로: (크기, mtime_ns, 파일 정체성)}` 색인을 만들고 `tool_result` hook에서 다시 만든다. 파일 정체성은
  `st_ino`이고 0이면 비교에서 뺀다(mtime을 보존한 교체를 잡기 위함). 다른 항목만 `created`·`modified`·`deleted`다. 바뀐 파일만 해시하고
  `HashCache`를 함께 써서 다음 run의 입력 해시가 다시 읽지 않게 한다. 내용으로 비교하지 않으므로 크기·mtime·정체성을 모두 보존한 바꿔
  쓰기는 놓친다(run과 같은 한계). 디렉터리 mtime으로 하위를 건너뛰지 않는다: 제자리 수정은 디렉터리 mtime을 바꾸지 않는다.
- 이전 관찰의 끝 색인을 다음 관찰의 시작으로 재사용하지 않는다. 두 호출 사이에 사람·다른 프로세스가 바꾼 것이 다음 명령의 창에 들어가기 때문이다.
- **설명된 변경**: 창 안(`seq_before < seq ≤ seq_after`)에 완료 이벤트가 있는 run의 출력(그 run 기록의 `outputs`의 경로·sha256)이나 가져오기
  (가져오기 기록의 `path`·`sha256`과 그 meta 파일)와 경로·바이트가 같은 변경은 `explained_by: {type: run 또는 dataset_import, id}`다. 창 안의
  journal은 시작 때 잰 journal 크기 뒤에 덧붙은 줄만 읽는다. 설명되지 않은 변경만 결정 1의 조건과 출처 추적(결정 8)에 쓴다.
- **창의 뜻**: 이 도구 호출이 도는 동안 관찰 범위에서 바뀐 것이다. 이 명령이 썼다는 뜻이 아니다. 한 aelix 세션의 모델 bash 호출끼리는
  겹치지 않지만, 같은 프로젝트의 다른 세션, 사람의 편집기, 앞선 명령이 남긴 백그라운드 프로세스, 다른 kit 실행이 그 창에 쓴 것도 들어간다.
  다른 확장의 `tool_call` 처리기가 사람의 승인을 기다리는 시간도 창에 든다.
- **기록 모양**: `changes`는 `{state: observed 또는 not_observed, reason, total, created, modified, deleted, unexplained, items, items_complete}`다.
  `items`는 경로의 코드 포인트 순으로 앞 50개이고 직렬화해 24 KiB를 넘으면 그 안에 드는 개수까지다. 항목은 `{path, change, bytes, sha256,
  hash, explained_by}`이고 `hash`는 `hashed`·`over_budget`·`withheld`(결정 5)·`unreadable`·`deleted`다. 인라인이 전부가 아니면 전체 목록
  (최대 10,000개)을 첨부 `changes.json`(정렬한 UTF-8·LF JSON)에 둔다. `items_complete`는 인라인과 첨부를 합쳐 모든 변경을 담았는지다.
  10,000개를 넘으면 수만 정확하고 `items_complete=false`다.

### 4. 명령이 가리킨 스크립트와 파일

- 가리기 전의 명령 글을 셸 단어로 나눈다(확장의 기존 POSIX·PowerShell 단어 읽기를 쓰고, 확신하지 못하는 줄은 공백으로 나눈다). 단어가 aelix의
  작업 디렉터리 기준으로 프로젝트 안의 일반 파일로 풀리면(경로 정체성, 관리 영역·`.venv` 제외) 그 파일을 가리킨 것이다.
  heredoc 본문과 `-c` 코드는 파일이 아니라 명령 글(결정 5)에 남는다.
- **스크립트**: 접미사가 `.py`·`.sql`·`.r`·`.jl`·`.sh`·`.ipynb`(대소문자 무시)인 파일은 `scripts`에 `{path, bytes, sha256, captured, changed_during,
  copy}`로 남긴다.
  - `tool_call` 때 있으면 그 바이트를 메모리에 읽어 두고, 기록할 때 첨부 `scripts/<정체성 경로>`로 쓴다(`captured: before`). 끝에 크기·mtime·
    정체성이 다르거나 없어졌으면 `changed_during: true`이며 실제로 실행된 바이트를 모른다.
  - 시작 때 없고 끝에 있으면(같은 명령이 heredoc으로 만든 뒤 실행) 끝의 바이트를 복사하고 `captured: after`다.
  - `sha256`은 복사한 바이트의 해시다. 사본은 바이트 그대로이며 가리지 않는다([ADR-0007](0007-line-endings.md), run 코드 스냅샷과 같음).
  - 상한은 8개, 파일당 1 MiB, 합 4 MiB다. 넘은 것은 경로·크기만 두고 `copy: null`, 사유 `over_budget`이다. 프로젝트 밖의 파일은 경로 없이
    개수만 둔다.
- **언급한 파일**: 스크립트가 아닌 일반 파일은 `mentioned`에 경로만 둔다(앞 20개와 총 수). 해시하지 않는다. 읽었다는 주장이 아니며
  출처 추적의 선이 되지 않는다.
- 이 사본은 run의 코드 스냅샷(`code_snapshot` 참조)이 아니며 재실행(`rerun_of`)의 대상이 되지 않는다.

### 5. 명령 글과 비밀 가림

명령 글은 어떤 것을 저장하거나 해시하기 전에 다음 규칙(`rules_version: 1`)으로 가린다. 가린 자리는 `[redacted]`이고 그 수를
`redaction.count`에 둔다. 가리기 전의 글·그 해시·그 길이는 어디에도 남기지 않는다(짧은 비밀의 해시는 추측할 수 있다).

1. 이름 규칙: 이름에 token·secret·password(passwd·passphrase)·credential·auth·api key·access key·private key가 들거나 `_KEY`로 끝나는 것
   (대소문자 무시, 구분자 `_`·`-` 무시). 확장의 `SECRET_NAME`을 넓힌 것이다.
2. 대입: 환경 변수(`NAME=value`, `export NAME=value`, `env NAME=value`, PowerShell `$env:NAME = 'value'`)와 코드 글의 `name = "value"`·
   `name: "value"`·`"name": "value"`에서 이름이 규칙에 맞으면 값.
3. 옵션: `--name=value`·`--name value`에서 이름이 규칙에 맞으면 값.
4. URL: 사용자 정보의 비밀번호(`scheme://user:…@host`)와 이름이 규칙에 맞는 질의 매개변수의 값.
5. 헤더: `Authorization`·`Proxy-Authorization`·`Cookie`·`X-Api-Key` 류 헤더의 값(따옴표 안 포함).
6. 알려진 형태: PEM 개인 키 블록, JWT 꼴(`eyJ`로 시작하고 `.`으로 나뉜 세 덩어리), `AKIA`+16자, `ghp_`·`gho_`·`github_pat_`·`glpat-`, `sk-`(`sk-ant-` 포함), `xox?-`, `AIza`+35자.

- 거부 목록이라 모든 비밀을 잡지 못한다(위치 인자의 비밀 `mysql -pVALUE`, 이름 없는 문자열, 인코딩한 값). 기록은 "가림을 적용했다"이지
  "비밀이 없다"가 아니다. 공유본에 넣지 않는 이유의 하나다(결정 10).
- 스크립트 사본(결정 4)은 가리지 않는다. 하드코딩한 비밀은 run 코드 스냅샷과 같은 위험이다.
- 비밀일 수 있는 이름의 파일(`.env`·`.env.*`, `*.pem`·`*.key`, `.netrc`·`.pgpass`, `id_rsa*`·`id_ed25519*`, `credentials*`, 이름에 `secret`)은
  변경 목록에 경로·변경 종류·크기만 두고 해시하거나 복사하지 않는다(`hash: withheld`).
- 작업 디렉터리는 프로젝트 상대 경로만 남긴다(결정 2). `session`은 다른 이벤트와 같은 세션 파일 경로다(C1에 이미 있는 값).
- 출력(stdout·stderr)은 남기지 않는다. 출력은 aelix 세션 기록에 있고 `session`과 `tool_call_id`로 찾는다.
- 명령 글은 실행 뒤 `tool_result`의 인자(다른 확장이 바꾼 뒤의 것)에서 읽는다.
- 기존 telemetry `summary`(가리지 않은 bash 명령 앞 300자)는 이 결정이 바꾸지 않는다(OQ6).

### 6. 비용 상한

| 단계 | 상한(제안) | 넘으면 |
|---|---|---|
| 앞·뒤 상태 보기 | 각각 파일 20,000개 또는 2초 | 그 관찰은 `changes.state=not_observed`, `reason=over_budget`. 같은 aelix 세션의 다음 bash 호출부터는 상태를 보지 않고 분석 실행 조건으로만 기록한다. 새 세션에서 다시 잰다 |
| 바뀐 파일 해시 | 한 관찰에 합 512 MiB 또는 5초 | 남은 파일은 `sha256=null`, `hash=over_budget` |
| 변경 목록 | 인라인 50개·24 KiB, 첨부 10,000개 | 수만 정확(`items_complete=false`) |
| 스크립트 사본 | 8개, 파일당 1 MiB, 합 4 MiB | 경로·크기만 |
| 명령 글 | 인라인 8 KiB(직렬화), 첨부 1 MiB | 앞 1 MiB만 두고 `truncated: true`(잘린 사실을 기록에 남김, ADR-0006 §10) |
| journal 잠금 대기 | 5초 | 기록하지 않음. 자기 폴더 삭제를 시도하고 결정 11의 안내에 사유를 남김 |

- 상태 보기·해시·쓰기는 aelix event loop가 아니라 스레드에서 한다. 앞 상태 보기는 명령 시작을 그만큼 늦춘다. 위 측정으로는 파일 1만 개에서
  호출마다 약 100 ms(두 번)다. 이 비용이 이 결정의 대가다.
- 쓰기가 없다고 확신할 수 있는 명령의 상태를 보지 않는 판정은 구현이 더할 수 있다. 확신하지 못하면 본다.
- 상한 값은 구현 PR의 측정 기록(파일 1천·1만·5만 개, 세 OS, cold·warm)으로 정한다(OQ4).

### 7. 읽기

- **reader**(`relations`): `observed_activity`를 아는 kind로 읽는다. `details.contract`가 `observed_activity/1`이 아니거나 필수 필드를 해석하지
  못하는 줄도 기록으로는 보이지만(목록·인용 대상) 관찰로 쓰지 않는다: 개수·출처 추적에서 빠지고 화면은 세부를 읽지 못했다고 적는다.
  상태 재생의 대상이 아니다(ADR-0006 §6 표의 "그 밖의 kind"). 철회·정정 대상이 아니다.
- **Workspace Timeline**: 행마다 종류 label `Observed activity`와 배지 `Not recorded by the kit`(글로도 표시), 가린 명령 앞부분, 바뀐 파일 수와
  그 가운데 설명되지 않은 수, 창 시각을 보인다. 종류 필터와 종류별 목록 페이지에 든다. 작성 주체는 관찰(`system`)로 보인다.
- **기록 페이지**(기존 관계 페이지): 가린 명령과 첨부 전문 링크, 작업 디렉터리, 창, 결과 상태, 스크립트 사본(코드 보기, `captured`·
  `changed_during`), 바뀐 파일(데이터셋이면 그 페이지, 설명된 변경은 그 run), 언급한 파일, 그리고 남기지 않은 것(결정 9)을 고정 문장으로 적는다.
- **Evaluation의 Record coverage**: 관찰 수, 설명되지 않은 변경이 있는 관찰 수, 변경을 보지 못한 관찰 수(`over_budget`)를 따로 센다.
  telemetry의 `Tool usage`·`Possible workarounds`는 그대로다.
- **`analytics_status`**(인자 없는 현황): 최근 기록 10개 목록에서 관찰을 빼고 한 줄로 센다:
  `observed outside the kit: N (latest eK; M changed files no run recorded) — part=records kind=observed_activity`. 0이면 줄이 없다.
  telemetry 수를 말하던 `bypass_note`는 이 줄로 바꾼다(관찰 기록 전의 telemetry 수는 Evaluation에 남는다). [A2](../CONTRACTS.md#a2-제한된-맥락기록근거-조회-kit-0110)의
  `part=records`는 `kind=observed_activity`로 관찰을 같은 cursor 규칙으로 읽는다.
- **맥락 블록**: 현재 세션의 관찰이 있을 때만 한 줄로 그 수와 최근 id, 입력·환경이 기록되지 않았으며 분석 단계는 `analytics_run`으로 하라는
  안내를 넣는다. 핵심 정보·열린 항목 목록에는 넣지 않는다.

### 8. 출처 추적(C18)

- 데이터 노드(경로·sha256)의 생산자 판정(`producer_state`, run 후보)은 바꾸지 않는다. 그와 따로, 같은 경로·sha256의 **설명되지 않은**
  `created`·`modified` 변경을 가진 관찰을 `observed_producers` 후보로 더한다. 선은 새 종류 `changed`(관찰 → 데이터, `basis: observed`,
  `confirmed: false`)이고 문장은 "Changed while this ran (observed)" 류다.
- 관찰은 확정 연결로 오르지 않는다. 생산자 판정을 `resolved`로 만들거나 run 후보를 대신하지 않으며 `checks`의 확정 연결 수에 들지 않는다.
- 관찰 노드는 run처럼 한 단계다. 출처 방향은 관찰에서 멈추고 입력이 기록되지 않았다고 적는다. 스크립트 사본은 노드가 아니라 관찰 노드의 값이다.
  사용처 방향은 설명되지 않은 변경 파일과 그 관찰을 인용한 기록이다.
- 확인 줄(`checks`)에 `observed`(경로에서 만난 관찰의 수, 이름은 앞 20개)를 더한다. 다른 줄과 하나의 점수로 합치지 않는다.
- 해시가 없는 변경(`over_budget`·`withheld`)은 후보가 아니다. 같은 바이트라는 관측이 없다. 지금 없는 파일(`Missing now`)의 데이터 노드에는
  그 경로를 `deleted`로 본 마지막 관찰을 덧붙일 수 있다.
- 기록이 관찰을 인용한 `event` 참조는 지금 규칙대로 판정한다. 정확한 대상을 찾았다는 확정 연결의 뜻(ADR-0006 §3)을 바꾸지 않는다. 화면은
  대상 종류로 그것이 관찰임을 보이고 `observed` 확인 줄이 따로 센다. 근거가 검증됐다는 뜻이 아니다.

### 9. 주장하지 않는 것

- **입력**: 무엇을 읽었는지. `mentioned`는 명령 글의 이름일 뿐이다.
- **환경**: 인터프리터·Python·패키지·`uv.lock`. 프로젝트 환경을 썼는지도 모른다.
- **재현성**: 재실행 대상이 아니고 재실행 비교가 없다. `analytics_run`의 `rerun_of`는 관찰을 받지 않는다.
- **실행한 바이트**: 사본은 시작(또는 끝)의 바이트다. `changed_during`이면 모른다. heredoc·`-c` 코드는 명령 글에만 있다.
- **인과**: 바뀐 파일은 그 창에 바뀐 것이며 그 명령이 썼다는 증거가 아니다.
- **완전성**: 관찰 범위 밖(관리 영역·`scripts/`·프로젝트 밖), 상한을 넘은 부분, 도구 호출 뒤의 백그라운드 쓰기, 크기·mtime·정체성을 보존한 바꿔 쓰기.
- **출력·검사·검토**: 출력 내용, 분석 방법 검사, 결론 검토. run의 `execution_status`·`recording_status` 축도 없다. `result.is_error`는 aelix가 준
  도구 결과일 뿐이다.

### 10. 공유본

- v1은 read·evidence 두 프로필 모두 관찰을 담지 않는다. `PROJECT_KINDS` 밖이라는 지금의 기본 제외 그대로이며 Workflow feedback·Tool usage와
  같은 내부 작업 기록으로 둔다([공유 build 설계](../design/PUBLICATION-BUILD.md) §5.3의 OQ3). 가림이 거부 목록이라 명령 글을 공유해도 된다고
  말할 수 없다. evidence 프로필에 넣는 안은 OQ1이다.
- 포함한 기록이 관찰을 인용하면 그 참조는 범위 밖 대상의 기존 표시(`not_included`, 종류·상태만, 이름·명령 없음)다. 공유본의 데이터 노드는
  관찰 후보를 보이지 않는다.
- [ADR-0014](0014-publication-record-contract.md) 결정 2의 닫힌 집합은 바꾸지 않는다. 관찰 줄은 내용 기준(`content_seq`)을 앞당기므로, 관찰만
  더해진 뒤 다시 build하면 내용 해시가 달라질 수 있다(Workflow feedback도 지금 그렇다).

### 11. #170의 알림·telemetry와의 관계

- #170의 알림과 이 기록은 같은 `tool_result` hook에서 일어난다. 알림이 붙는 호출을 관찰로 기록했으면 알림 끝에 관찰 id를 덧붙이고(예:
  `observed as e57: command, scripts and changed files only`), 기록하지 못했으면 짧은 사유(`over budget`·`journal busy`)를 덧붙인다. 기록을
  기다리느라 알림을 늦추지 않는다. 알림은 계속 `analytics_run`을 권한다: 관찰은 run을 대신하지 않는다.
- 이 결정은 알림의 조건(`python_via_bash`)을 넓히지 않는다. python이 아닌 명령이 설명되지 않은 변경을 남긴 경우의 알림은 OQ3이다.
- telemetry는 그대로다(형식, `python_via_bash`·`wrote_managed_path`, Workspace의 `Tool usage`·`Possible workarounds`). 관찰은 telemetry를
  읽지 않고 telemetry도 관찰을 가리키지 않는다. 분석 실행의 판정은 같은 함수를 쓴다.

### 12. 버전과 호환

- 이 kind의 첫 writer는 그 쓰기 전에 표식의 `min_writer_version`을 자기 버전으로 올린다(ADR-0006 §1의 3항, `Writer`의 `requires`). 사람이
  요청하지 않은 부수 쓰기가 floor를 올린다는 점이 이 kind의 특징이다.
- kit 번호: 소유자 결정(2026-11-09)으로 0.32.1을 올리지 않으므로 floor 값은 0.32.1이다. 이는 ADR-0006 §1의 3항("규칙을 더하는 PR은 같은 PR에서
  `KIT_VERSION`을 올린다")의 예외다. 구현 전의 main 0.32.1 체크아웃은 이 kind를 모르면서 같은 번호를 단다. 0.32.1은 게시·후보 기록 전이고
  0.32.0은 게시하지 않으므로 게시된 kit 사이에 같은 번호·다른 규칙은 생기지 않는다. OQ5에서 확인한다.
- 0.32.1 미만의 kit(게시된 것은 없다)은 관찰 줄을 `unsupported`(`unknown_kind`)로 보존·계수하고(ADR-0006 §1의 4항), floor가 오른 뒤에는 쓰기를
  `writer_too_old`로 거절한다. 구현 전의 0.32.1 체크아웃은 쓸 수 있고 관찰 줄을 `unsupported`로 보인다. 그 체크아웃은 이 kind를 쓰지 않으므로
  어긋난 줄이 생기지 않는다.
- `.analytics/observed/`는 run의 출력 비교와 입력 캡처가 이미 빼는 `.analytics/` 아래다. run·Workspace 렌더·전환의 동작은 바뀌지 않는다.
  새 템플릿의 `.gitignore`에 넣지 않는다: CONTEXT 버전처럼 프로젝트의 이력이다.
- schema(`event_2`의 kind와 details)·정상/음성 예제·[현재 계약](../CONTRACTS.md)·[용어집](../TERMINOLOGY-AND-LANGUAGE.md)은 구현 PR이 함께 바꾼다.

## 대안과 선택 이유

| 대안 | 검토 결과 |
|---|---|
| 알림만(#170, 권장안 (a)) | 소유자가 (b)를 함께 정했다. 이미 한 작업의 흔적이 남지 않는다 |
| 별도 가족 `analytics.observed_activity/1` 파일과 완료 이벤트(run 기록 방식) | 새 참조 type, 두 단계 커밋, reader의 파일 읽기가 는다. 관찰은 작은 요약과 몇 개의 첨부라 이벤트 한 줄로 충분하고, ADR-0006이 새 kind를 이 용도로 열어 두었다 |
| telemetry에 필드를 더함 | 보조 로그는 seq·reader·출처 추적·공유 규칙 밖이다. 사용량 측정과 출처 기록을 섞는다 |
| 관찰을 run으로 합성(`runs/<id>/`, 상태 `observed`) | run은 실행한 바이트·입력·환경·재실행을 약속한다(C2). 빈 칸의 run은 그 약속을 깨고 재실행과 확정 연결의 오해(Q4)를 만든다 |
| 모든 bash 호출을 기록 | `ls`·`git`·`grep`가 Timeline을 덮는다. 기록할 사실이 없는 호출이다 |
| `python_via_bash`일 때만 기록 | `curl -o data/…`·`cp`·`duckdb`로 바꾼 데이터를 놓친다 |
| 입력 캡처(OS 추적, `sitecustomize`·`PYTHONPATH` 주입으로 capture hook을 심음) | 플랫폼 포트(ADR-0003의 네 포트) 밖의 OS별 기능이거나 사용자의 프로그램 동작을 바꾼다. 실행을 kit에 맞추는 길은 `analytics_run`이다 |
| 파일 시스템 감시(FSEvents·inotify·ReadDirectoryChangesW) | 상주 감시자, OS별 구현, 이벤트 합침·누락. 이식 경계 밖이다 |
| 디렉터리 mtime으로 하위 생략 | 제자리 수정을 놓친다 |
| 앞뒤 내용 전체 해시 | 큰 `data/`에서 호출마다 전체를 읽는다 |
| 출력 저장 | 데이터 행·개인 정보가 들고 크다. aelix 세션에 이미 있다 |
| 명령 글을 가리지 않음, 또는 해시만 | 자격 증명이 git이 추적하는 journal에 남는다 / 해시만으로는 무엇을 했는지 읽을 수 없다 |
| 관찰을 생산자 `resolved`나 확정 근거로 | 창의 관찰은 인과가 아니다. Q4의 잘못된 확정 연결이다 |

## 결과와 영향

- bash로 만든 파일이 "생산자 없음"에서 "관찰된 활동 eN의 창에 바뀜"으로 보인다. 발견은 그 관찰을 인용할 수 있고, 화면은 그것이 관찰임을 보인다.
- 비용: 기록 대상이 될 수 있는 bash 호출마다 상태 보기 두 번(측정한 기계의 파일 1만 개에서 약 100 ms), 바뀐 파일의 해시, journal 한 줄.
  큰 `data/`에서는 변경을 보지 못한 관찰이 된다.
- kit이 쓰는 journal 줄이 늘고(세션당 수십) Timeline이 길어진다. 맥락 블록은 현재 세션의 한 줄만 는다.
- 첫 관찰이 표식 floor를 0.32.1로 올린다. 가린 명령 글과 스크립트 사본이 git이 추적하는 프로젝트 이력(journal·`.analytics/observed/`)에 남는다.
- telemetry의 가리지 않은 명령 앞 300자는 그대로다(OQ6).

## 소유자에게 묻는 것

| OQ | 질문 | 권장안 |
|---|---|---|
| OQ1 | 공유본에 관찰을 담는가 | v1은 두 프로필 모두 담지 않는다(결정 10). 필요해지면 evidence 프로필에 가린 명령·변경 목록만(스크립트 사본 제외) 후속으로 |
| OQ2 | 관찰 대상을 넓히는가: write·edit 도구로 관찰 범위를 직접 바꾼 것, 사람의 `!` 명령 | 둘 다 v1에서 하지 않는다. write·edit는 경로를 알아 싸게 같은 kind로 더할 수 있으나 Timeline이 늘어 후속으로. `!` 명령은 사람의 셸을 감싸야 해서 하지 않는다 |
| OQ3 | python이 아닌 명령이 설명되지 않은 변경을 남겼을 때도 #170처럼 bash 결과에 안내를 붙이는가 | 붙인다(한 줄: run이나 가져오기로 기록되지 않았다, `analytics_import`·`analytics_run`). #170의 수용 조건("다른 명령에는 붙지 않는다")을 바꾸므로 소유자 결정이다 |
| OQ4 | 큰 `data/`의 비용 상한 | 파일 2만 개·2초를 넘는 프로젝트는 그 세션에서 변경 관찰을 끈다(결정 6). 값은 구현 측정으로 확정. 대안: 상한 없이 항상 봄(호출마다 수백 ms–초), 또는 `data/`만 봄 |
| OQ5 | ADR-0006 §1의 3항 예외: 번호를 올리지 않고 floor 0.32.1 | 받아들인다. 게시된 kit 사이의 규칙 차이가 생기지 않는다 |
| OQ6 | telemetry `summary`의 가리지 않은 bash 명령 300자 | 같은 가림 규칙을 같은 구현 PR에서 telemetry에도 적용한다(C5의 동작 변경) |

## 구현 순서와 확인할 것

둘 다 0.32.1에 넣는다. 기록 형식 PR이므로 각 PR은 구현 리뷰(독립 1회·Codex 1회)와 수정 diff 확인을 받고, 세 OS CI와
측정 기록(`docs/verification/`의 md+JSON)을 남긴다. CHANGELOG는 0.32.1 절에 적는다.

1. PR-1 쓰기와 최소 읽기: 관찰의 판정·상태 보기·가림·첨부를 stdlib 전용 kit 모듈에 두고 확장이 aelix 프로세스 안에서 로드한다(계약 A의 import
   목록, `tests/test_layers.py`, [아키텍처](../ARCHITECTURE.md) §11을 함께 고침). 확장의 `tool_call`·`tool_result` hook, writer의 kind·floor,
   schema·예제, reader의 kind 인식, `analytics_status`의 수 줄과 A2 조회, #170 알림의 id.
2. PR-2 화면과 출처: Workspace Timeline·기록 페이지·Record coverage, 맥락 블록의 한 줄, C18의 관찰 후보·`changed` 선·`observed` 확인 줄.

확인할 것:

- 기록 조건: `python_via_bash`, 설명되지 않은 변경, kit 명령 하나(상태를 보지 않음), `ls`(기록 없음), 다른 확장이 막은 호출(기록 없음),
  읽기 전용·옛 형식 프로젝트(쓰기와 floor 변경 없음), 프로젝트 밖 작업 디렉터리에서 프로젝트를 가리키지 않는 python(기록 없음).
- 상태: 만들기·고치기·지우기, 링크 경계, mtime을 보존한 교체, bash 안의 `analytics run`·`analytics data import`로 설명된 변경, 창 안 다른
  프로세스의 쓰기(창의 변경으로 기록).
- 스크립트: 시작 때 있던 파일, 같은 명령이 heredoc으로 만든 파일(`captured: after`), 창 안에서 바뀐 파일(`changed_during`), 상한.
- 상한: 상태 보기 초과와 세션 안의 생략, 해시 초과, 50개·10,000개 넘는 변경, 8 KiB·1 MiB 넘는 명령, 줄 상한.
- 가림: 규칙마다 canary 값(시험 값의 이름은 value·marker·canary)이 프로젝트 트리 어디에도 원문·해시로 남지 않음, 비밀 이름 파일의 `withheld`.
- 커밋: 이벤트 전 실패(폴더 삭제 시도), 이벤트 실패(이벤트 없는 폴더는 세지 않음), journal 잠금 시간 초과, 도구 결과 불변.
- 호환: 관찰 줄이 든 journal을 0.32.0 후보로 읽기(`unsupported` 계수), floor 뒤의 `writer_too_old`, 구현 전 0.32.1 체크아웃의 쓰기.
- 읽기·출처: 후보가 더해져도 `producer_state` 불변, 확정 연결 수 불변, `Missing now`의 삭제 관찰, 공유 build가 관찰을 담지 않고 명령
  canary가 패키지에 없음, `content_seq`가 관찰 줄을 셈.
- 측정: 파일 1천·1만·5만 개의 상태 보기와 해시 비용, bash 호출마다 늘어난 지연, 관찰 줄 크기를 세 OS(CI)에서.

이 PR의 문서 검사와 설계 리뷰는 runtime·세 OS·실제 모델의 통과 증거가 아니다.
