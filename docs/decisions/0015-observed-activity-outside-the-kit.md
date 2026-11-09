# ADR-0015: kit 밖 실행의 관찰 기록 — `observed_activity`

Date: 2026-11-09 · 개정: 2026-11-10(설계 리뷰 반영)

Status: Proposed — [#171](https://github.com/handochan/aelix-analytics/issues/171)의 결정 PR이다. 소유자 결정(2026-11-09, #171의 권장안 (b),
[첫 출시 0.32.1의 범위](https://github.com/handochan/aelix-analytics/issues/24#issuecomment-6742451632))의 기록 형식을 구체화한다.
새 기록 종류를 더하는 결정이므로 이 PR의 리뷰가 [리뷰 예산](../PROJECT-OPERATIONS.md#리뷰-예산)의 설계 리뷰(독립 1회·Codex 1회)다.
두 설계 리뷰(2026-11-09, 둘 다 request changes)의 지적을 이 개정에 반영했고 수정 diff 확인을 기다린다. 소유자 질문 OQ1–OQ8은
소유자 결정(2026-11-09, 권장안 채택 지시)으로 정했다(아래 "소유자 결정"). 채택은 구현 완료가 아니다. 이 PR은 runtime·schema·kit 버전·표식을 바꾸지 않는다.

## 맥락과 현재 코드

기준은 main `6fd956c`(kit 0.32.1)이다.

- 확장은 모든 도구 호출을 `.analytics/telemetry.jsonl`에 한 줄로 남긴다(`extension/aelix_analytics.py`의 `on_tool_end`·`summarize_call`, 1728행).
  bash는 명령 앞 300자(가리지 않음)와 `python_via_bash` 표시, write·edit는 경로와 `wrote_managed_path`다. 이 로그는 사용량을 재는 보조 로그다
  ([계약 C5](../CONTRACTS.md#c5-보조-로그)). journal seq가 없고, 공통 reader·출처 추적·Timeline은 읽지 않는다. Workspace Evaluation의
  `Possible workarounds`와 `analytics_status`의 한 줄 안내(`bypass_note`, 795행)에만 나온다. 새 템플릿의 `.gitignore`는 이 파일을 무시하지 않으므로,
  가리지 않은 명령 300자는 지금도 git에 커밋될 수 있다.
- 그래서 bash로 돌린 Python이 `data/derived/x.parquet`를 쓰면 그 파일은 데이터셋 목록에 있지만 생산자가 없다([C18](../CONTRACTS.md#c18-출처-추적-kit-0240-100)의
  `producer_state=none`). 그 수치를 인용한 발견도 근거 없이 남는다. #170의 알림은 그 자리에서 모델에게 알릴 뿐 흔적을 남기지 않는다.
- run은 실행 전후로 프로젝트 이름 공간의 `{경로: (크기, mtime_ns)}`를 비교하고(`runner.tree_stat`, 129행), 링크 경계가 생기거나 없어진 경우를
  따로 다룬 뒤(`rekeyed`, `diff_outputs`의 made/gone 규칙, 179행) 바뀐 파일만 해시한다. 해시 캐시(`core.HashCache`, 1184행)의 키는 경로와
  `(크기, mtime_ns)`뿐이다. `Project.walk()`는 디렉터리의 항목을 모두 읽어 정렬한 뒤 내놓는다. 코드 스냅샷·입력·환경은 run 안의 capture hook이 남긴다.
- aelix 0.1.0b2: `tool_call` hook은 각 도구 실행 전에 기다려 부르며 막을 수 있다. `tool_result` hook은 실행 뒤에 기다려 부르며 결과를 덧붙이거나
  바꾼다(`ToolResultPatch`). hook 등록의 기본 `error_mode`는 `throw`라 처리기의 예외가 그 턴을 깬다. `bash`·`write`·`edit` 도구는
  `execution_mode="sequential"`이라 그것이 든 한 묶음의 도구 호출은 차례로 돈다(`aelix_agent_core/loop.py`). 사람의 `!` 명령에는 실행 전
  `user_bash` 처리기만 있고 실행 뒤 hook이 없다.
- #170([PR #176](https://github.com/handochan/aelix-analytics/pull/176), 리뷰 중)은 같은 `tool_result` hook으로, kit을 거치지 않은 Python 실행
  (`python_outside_the_kit`: `PYTHON_IN_SHELL`이고 `KIT_IN_SHELL`·`bin/analytics`·kit 프로그램 `analytics` 호출이 없음)의 bash 결과 끝에
  "기록되지 않았다"는 안내를 붙인다. 막지 않는다.
- 확장은 aelix 프로세스 안에서 stdlib 전용 kit 모듈 넷(`core`·`_platform`·`relations`·`writer`)만 import한다([계약 A](../CONTRACTS.md#a-aelix-확장-표면--쓰는-것-전부)).
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

1. 활성 프로젝트가 있고 이 kit이 쓰는 프로젝트다. 판정은 telemetry와 같은 부수 쓰기 규칙(`admit(..., side=True)`)이며 `tool_call` 때 한다.
   읽기 전용·옛 형식·전환 미완료·표식 충돌이면 상태를 보지도 쓰지도 않는다.
2. 도구가 aelix의 `bash`이고 그 호출이 실제로 실행됐다(`tool_result`가 왔다). 다른 확장의 `tool_call`이 막은 호출, 취소로 `tool_result`가
   오지 않은 호출은 기록하지 않는다(결정 6에서 남은 앞 상태를 버림).
3. 다음 가운데 하나가 참이다(`details.reasons`).
   - `analysis_command`: #170과 같은 판정 `python_outside_the_kit`이 참이고, 줄의 Python 명령이 모두 패키지·환경 관리나 버전 조회
     (`pip`·`uv pip`·`uv sync|add|remove|lock`·`--version`·`-V`)만은 아니며, aelix의 작업 디렉터리가 프로젝트 안이거나 명령이 프로젝트 안의
     파일을 가리킨다(결정 4). kit 프로그램과 다른 Python을 섞은 줄은 그 판정대로 kit의 것이지만, 아래 `changes`가 그 줄의 다른 쓰기를 잡는다.
   - `changes`: 결정 3의 관찰 범위에서 바뀐 파일이 있고, 그 가운데 하나라도 같은 창에 kit이 기록한 run·가져오기로 설명된다고 확인되지 않았다
     (`unexplained` 또는 `unknown`).
   - `observation_off`: 이 호출에서 그 세션의 변경 관찰을 껐다(결정 6). 이 사실은 다른 조건과 관계없이 한 번 기록한다.

모두 아니면(`ls`, `git status`, `pip install`, `analytics run …`만 한 호출) 기록하지 않는다. 앞뒤 상태를 아예 보지 않는 생략은 **줄 전체가 kit CLI를 부르는
단순 명령 하나**(`analytics`·`bin/analytics`·`python -m aelix_analytics`, 앞의 `uv run`과 그 옵션 포함)일 때뿐이다. `user_bash`의 작성 주체 판정은
`python -c 'import aelix_analytics; …'`도 kit 명령으로 받지만, 그 판정은 run 기록을 보장하지 않으므로 생략에 쓰지 않는다. `-c` 코드와 SDK import는
상태를 본다.

범위 밖(OQ2): write·edit 도구의 직접 쓰기(telemetry의 `wrote_managed_path`는 그대로), 사람의 `!`·`!!` 명령(실행 뒤 hook이 없고, 관찰하려면
사람의 셸을 감싸야 해서 A의 `user_bash` 범위 결정과 충돌). 그 밖에 다른 확장·MCP 도구, aelix 밖 에이전트 호스트의 셸도 관찰하지 않는다.

### 2. 저장 형식: `analytics.event/2`의 새 kind `observed_activity`

- 새 기록 가족을 만들지 않는다. [ADR-0006](0006-common-record-contract.md) §1의 2항이 #13 뒤의 기능에 허용한 새 kind로 `observed_activity`를 더한다.
  공통 필드(`schema`·`seq`·`id`·`recorded_at`·`kind`·`actor`·`text`·`refs`)의 뜻은 그대로다.
- 사람·에이전트의 기록 kind(`RECORD_KINDS`)에 넣지 않는다. 공통 writer는 이 kind를 관찰 응용 기능의 잠금 문맥(operation `observe`)에서만 받고
  details를 검사한다(`publication`이 `build_publication`에서만 쓰이는 것과 같음). `analytics record`·SDK·모델 도구로 관찰을 만들 수 없다.
- 작성 출처(`Origin`)는 기록을 쓴 쪽이다: `actor=system`, `actor_basis=declared`, `means=hook`, `session`은 그 aelix 세션. 명령을 실행한 쪽은
  `details.performer`에 따로 둔다. 관찰은 에이전트가 쓴 기록이 아니므로 그 수에 섞지 않는다.
- `text`는 kit이 만드는 영어 한 줄이다: `Observed outside the kit: bash "<가린 명령 앞 160자>" — N files changed while it ran`. 사용자 글이 아니다.
- `refs`는 빈 목록이다. 입력을 모르므로 `uses`·`derived_from`을 만들지 않는다. 다른 기록은 이 관찰을 기존 `event` 참조(`e<seq>`)로
  인용할 수 있다. 새 참조 type은 없다.
- `activity_id`는 `act-` 뒤에 소문자 Crockford base32 ULID 26자다(`^act-[0-9a-hjkmnp-tv-z]{26}$`, `pub-`·`att-`와 같은 규칙). `tool_call` 때 만들고
  모든 관찰에 있다. 순서는 seq이고 ID의 시각 성분이 아니다. 재시도 키가 아니며 `request_id`도 쓰지 않는다.
- 첨부(가린 명령 전문, 긴 목록, 스크립트 사본)가 있으면 `.analytics/observed/<activity_id>/`를 배타적으로 만들어 파일을 쓰고, 그다음
  journal 잠금 안에서 이벤트를 덧붙인다. **이벤트가 커밋 지점이다.** 이벤트 없는 폴더는 `recording_status=incomplete`
  (`no_completion_event`)이고 개수·출처 추적에 쓰지 않는다. 이벤트 전에 실패하면 자기 폴더의 삭제를 시도하고, 다른 폴더는 자동으로 정리하지 않는다.
- **줄 예산**: 직렬화한 `details`는 24 KiB 이하다. 칸마다 상한(`text` 300 B, 명령 4 KiB, 변경 목록 12 KiB, 스크립트 목록 3 KiB, 언급한 파일 2 KiB)을 두고
  넘는 부분은 첨부(`command.txt`·`changes.json`·`files.json`)로 보내 그 sha256을 `captures`에 둔다. 그래도 줄 상한(50 KiB, [C1](../CONTRACTS.md#c1-이벤트-journaljsonl))에
  걸리면 수와 첨부 digest만 남긴 최소 details로 쓴다. 줄 상한 때문에 관찰을 버리지 않는다.
- 관찰의 실패는 도구 결과의 내용·오류 여부를 바꾸지 않는다(#170의 안내 줄은 결정 11).

새 writer가 쓰는 `details`는 다음과 같다. 필드는 모두 필수이며 모르는 값은 null이다.

| 필드 | 계약 |
|---|---|
| `contract` | `observed_activity/1` |
| `activity_id` | 위의 ID |
| `performer` | `{actor: "agent", tool: "bash", tool_call_id}`. `tool_call_id`는 aelix가 준 불투명 값이며 세션 기록에서 출력을 찾는 데 쓴다 |
| `started_at`, `ended_at`, `duration_ms` | 앞 상태를 보기 직전과 뒤 상태를 다 본 때(offset 포함). 명령 자체의 시작·끝이 아니라 관찰 창이다 |
| `cwd`, `cwd_outside` | aelix 작업 디렉터리의 프로젝트 상대 POSIX 경로(루트는 `.`)와 `false`. 밖이면 `null`과 `true`. 절대 경로는 남기지 않는다 |
| `command` | 결정 5로 가린 최종 명령: `{text, bytes, sha256, inline_complete, truncated, changed_after_start, redaction: {rules_version, count}}`. `bytes`·`sha256`은 **가린** 전문의 것 |
| `result` | `{is_error, exit_code}`. aelix가 준 것만이고 모르면 null. 출력 내용은 남기지 않는다 |
| `reasons` | 결정 1의 참인 조건 목록: `analysis_command`, `changes`, `observation_off` |
| `window` | `{seq_before, seq_after}`: 창의 시작과 끝에 같은 꼬리 읽기로 얻은 journal의 마지막 완료 줄 seq(결정 3) |
| `changes` | 결정 3 |
| `scripts`, `mentioned` | 결정 4 |
| `session_observation` | 이 호출 뒤 그 세션의 변경 관찰: `on` 또는 `off`(결정 6) |
| `captures` | 첨부마다 `{path, role, bytes, sha256}`. `role`은 `command`·`changes`·`files`·`script`. `path`는 `.analytics/observed/<activity_id>/` 기준. 없으면 빈 목록 |

### 3. 앞뒤의 파일 상태

- **범위**는 run의 출력 비교 대상과 같은 이름 공간이다([계약 B](../CONTRACTS.md#b-프로젝트-파일-규약): `.venv .analytics runs workspace scripts .git
  __pycache__ …`와 프로젝트 자신의 파일을 뺀 모든 파일, 밖을 가리키는 링크 경계 포함). 경로는 경로 정체성(E)이라 관찰의 경로와 run의 입력·출력
  경로가 같은 키다. 대부분은 `data/`이고 `outputs/` 같은 사용자 폴더도 든다. `scripts/`는 코드라 빠진다(명령이 가리킨 스크립트는 결정 4).
  관리 영역(`runs/`·`.analytics/`·`workspace/`·journal·표식)의 손 변경을 감지한다고 말하지 않는다.
- **색인과 비교 규칙은 run과 하나다.** 구현 PR은 `tree_stat`·`rekeyed`·`diff_outputs`의 규칙을 stdlib 전용 모듈로 옮겨 run과 관찰이 함께 쓴다.
  색인은 `{정체성 경로: (크기, mtime_ns, st_ino)}`이고 `st_ino`가 0이면 그 칸을 비교하지 않는다. 링크 경계 규칙도 run과 같다: 창에서 새로 생긴
  경계 아래 파일은 창 시작(파일 시스템 시계의 오차를 둔 시각) 뒤에 수정된 것만 `created`, 없어진 경계 아래 파일은 실제 경로에 파일이 없을 때만
  `deleted`, 실제 위치가 그대로인 파일은 새 경로로 다시 대응한다(`rekeyed`). 생기거나 없어진 경계는 `changes.links_changed`에 경로로 센다.
- **상한은 나열하면서 센다.** 관찰의 상태 보기는 디렉터리를 정렬하기 전, 항목을 하나 읽을 때마다 항목 수와 시간·취소 표지를 확인하고 상한에서 멈춘다.
  한 디렉터리에 항목이 아주 많아도 상한을 넘겨 읽지 않는다. 멈춘 색인은 비교하지 않는다(`not_observed`).
- **읽지 못한 것은 지운 것이 아니다.** 앞에 있던 경로의 뒤 stat이 없음 말고 다른 오류(Windows의 공유 위반 등)로 실패하면 `unreadable`이며 `deleted`가 아니다.
- **해시**: 바뀐 파일은 캐시를 보지 않고 바이트를 읽어 해시하고, 그 결과로 캐시 항목을 새로 쓴다(캐시 키가 `(크기, mtime_ns)`라 정체성만 바뀐 교체에
  옛 해시를 돌려주기 때문이다). 크기·mtime이 같고 정체성만 바뀐 파일의 새 해시가 앞 stat에 맞는 캐시 해시와 같으면 바뀐 것이 아니라 목록에서 뺀다.
  해시 전과 후의 stat이 뒤 색인의 값과 다르면 `hash: unstable`, `sha256: null`이다(창 밖의 바이트를 창 안 변경에 붙이지 않음).
- 이전 관찰의 끝 색인을 다음 관찰의 시작으로 재사용하지 않는다. 두 호출 사이에 사람·다른 프로세스가 바꾼 것이 다음 명령의 창에 들어가기 때문이다.
- **journal 창**: 앞과 뒤에서 journal의 끝(마지막 64 KiB)을 한 번씩 읽어, 마지막 LF까지의 바이트 위치와 그 앞 마지막 완료 줄의 seq를 같은 읽기에서 얻는다.
  뒤 읽기는 뒤 색인을 다 만든 직후이며 창의 끝이다. 창의 줄은 두 위치 사이의 완전한 줄이다. 전체 journal을 다시 읽지 않는다.
- **설명**: 변경마다 `explanation`은 셋 가운데 하나다.
  - `explained`: 창의 줄에 완료 이벤트가 있는 run의 출력(그 run 기록의 `outputs`의 경로·sha256)이나 가져오기(가져오기 기록의 `path`·`sha256`과
    그 meta 파일)와 경로·바이트가 같다. `by: {type: run 또는 dataset_import, id}`.
  - `unexplained`: 창의 줄을 모두 읽었고, 해시가 있으며, 어느 것과도 맞지 않는다.
  - `unknown`: 해시가 없거나(`unstable`·`over_budget`·`withheld`·`unreadable`), 창의 줄이나 그 run 기록을 읽지 못했다. 사유를 둔다.

  창 안에서 시작해 창 뒤에 끝난 run의 출력은 `unexplained`로 남을 수 있다. reader는 같은 경로·바이트의 run 후보가 있으면 관찰 쪽을 낮춘다(결정 8).
- **창의 뜻**: 이 도구 호출이 도는 동안 관찰 범위에서 바뀐 것이다. 이 명령이 썼다는 뜻이 아니다. 한 aelix 세션의 모델 bash 호출끼리는
  겹치지 않지만, 같은 프로젝트의 다른 세션, 사람의 편집기, 앞선 명령이 남긴 백그라운드 프로세스, 다른 kit 실행이 그 창에 쓴 것도 들어간다.
  다른 확장의 `tool_call` 처리기가 사람의 승인을 기다리는 시간도 창에 든다.
- **기록 모양**: `changes`는 `{state: observed 또는 not_observed, reason, total, created, modified, deleted, unreadable, unexplained, unknown,
  links_changed, items, items_complete}`다. `items`는 경로의 코드 포인트 순이고 줄 예산(12 KiB) 안에 드는 앞부분이다. 항목은 `{path, change, bytes,
  sha256, hash, explanation}`이고 `hash`는 `hashed`·`unstable`·`over_budget`·`withheld`·`unreadable`·`deleted`다. 인라인이 전부가 아니면 전체 목록
  (최대 10,000개)을 첨부 `changes.json`(정렬한 UTF-8·LF JSON)에 둔다. `items_complete`는 인라인과 첨부를 합쳐 모든 변경을 담았는지다.

### 4. 명령이 가리킨 스크립트와 파일

- **어느 명령인가**: 저장하는 명령은 실행 뒤 `tool_result`의 인자(다른 확장이 바꾼 뒤의 최종 명령)다. 스크립트도 최종 명령에서 찾는다.
  `tool_call` 때 본 명령과 글자가 다르면 `command.changed_after_start: true`이고, 시작 바이트는 최종 명령에도 있는 이름에만 쓴다.
- **이름 해석**: 가리기 전의 최종 명령을 셸 단어로 나눈다(확장의 기존 POSIX·PowerShell 단어 읽기를 쓰고, 확신하지 못하는 줄은 공백으로 나눈다).
  상대 경로는 aelix 작업 디렉터리에서 풀되, 명령 안의 글자 경로 `cd X`·`pushd X`·`Set-Location X`는 따라간다. 변수·치환·`cd -`처럼 따라갈 수 없는
  이동 뒤의 상대 이름은 풀지 않는다(경로를 두지 않고 `unresolved` 수만). 풀린 이름이 프로젝트 안의 일반 파일(경로 정체성, 관리 영역·`.venv` 제외)이면
  그 파일을 가리킨 것이다. heredoc 본문과 `-c` 코드는 파일이 아니라 명령 글(결정 5)에 남는다.
- **스크립트**: 접미사가 `.py`·`.sql`·`.r`·`.jl`·`.sh`·`.ipynb`(대소문자 무시)인 이름은 `scripts`에 `{name, path, bytes, sha256, hash, captured, stable,
  copy, outside}`로 남긴다.
  - `tool_call` 때 있으면 그 바이트를 메모리에 읽어 두고(`captured: before`), 끝에 크기·mtime·정체성이 그대로면 `stable: true`다. 다르거나
    없어졌으면 `stable: false`이며 실제로 실행된 바이트를 모른다. `stable: true`도 실행된 바이트라는 보증은 아니다(결정 9).
  - 시작 때 없던 이름(풀린 경로를 시작 때 보관해 둠)이 끝에 있으면(같은 명령이 heredoc으로 만든 뒤 실행) 끝의 바이트를 읽고 `captured: after`다.
  - `sha256`은 읽은 바이트의 해시다. 같은 관찰 안의 같은 바이트는 사본 하나를 함께 가리킨다.
- **사본을 만들지 않는 것**: 사본은 git이 추적하는 `.analytics/observed/`에 남으므로, 다음은 경로·크기·(허용되면) 해시만 두고 `copy: null`과 사유를 둔다.
  1. git이 무시하는 파일: 프로젝트가 git 작업 트리면 사본 후보를 `git check-ignore` 한 번으로 묻는다. git을 쓸 수 없으면 새 템플릿의 `.gitignore`가
     무시하는 자리(`data/`·`workspace/`·`.venv/` 등)로 판정한다. 사유 `ignored`.
  2. 비밀일 수 있는 이름의 파일(결정 5). 해시도 두지 않는다(`hash: withheld`).
  3. 바이트에 결정 5의 가림 규칙이 걸리는 파일. 해시도 두지 않는다(`hash: withheld`, 사유 `secret_like`). 가린 명령 글과 그 파일의 해시가 함께
     남으면 짧은 비밀을 추측할 수 있기 때문이다.
  4. `.ipynb`: 셀 출력에 데이터가 들 수 있어 해시만 둔다(사유 `notebook`).
  5. 상한 초과: 8개, 파일당 1 MiB, 합 4 MiB(사유 `over_budget`).

  위에 걸리지 않은 스크립트만 첨부 `scripts/<정체성 경로>`에 바이트 그대로 둔다([ADR-0007](0007-line-endings.md), run 코드 스냅샷과 같음).
- **프로젝트 밖의 스크립트**(OQ7): 파일 이름(마지막 구성 요소)·바이트 수·sha256만 둔다(`outside: true`). 경로와 사본은 없다. 위 2·3에 걸리면 해시도 없다.
- **언급한 파일**: 스크립트가 아닌 프로젝트 안의 일반 파일은 `mentioned`에 경로만 둔다(줄 예산 안의 앞부분과 총 수, 나머지는 `files.json`).
  해시하지 않는다. 읽었다는 주장이 아니며 출처 추적의 선이 되지 않는다.
- 사본은 run의 코드 스냅샷(`code_snapshot` 참조)이 아니며 재실행(`rerun_of`)의 대상이 되지 않는다.

### 5. 명령 글과 비밀 가림

명령 글은 어떤 것을 저장하거나 해시하기 전에 다음 규칙(`rules_version: 1`)으로 가린다. 가린 자리는 `[redacted]`이고 그 수를
`redaction.count`에 둔다. 가리기 전의 글·그 해시·그 길이는 어디에도 남기지 않는다(짧은 비밀의 해시는 추측할 수 있다).

1. 이름 규칙: 이름에 token·secret·password(passwd·passphrase)·credential·auth·api key·access key·private key·signature가 들거나 `_KEY`로 끝나는 것
   (대소문자 무시, 구분자 `_`·`-` 무시). 확장의 `SECRET_NAME`을 넓힌 것이다.
2. 대입: 환경 변수(`NAME=value`, `export NAME=value`, `env NAME=value`, PowerShell `$env:NAME = 'value'`)와 코드 글의 `name = "value"`·`name: "value"`,
   큰따옴표·작은따옴표 사전 키(`{"name": …}`·`{'name': …}`)에서 이름이 규칙에 맞으면 값.
3. 옵션: `--name=value`·`--name value`에서 이름이 규칙에 맞으면 값. `-u`·`--user`의 `user:password`(curl 등)는 비밀번호 부분.
4. URL: 사용자 정보의 비밀번호(`scheme://user:…@host`)와 **모든 질의 매개변수의 값**(이름은 둠: SAS `sig`, `X-Amz-Signature`, `X-Goog-Signature` 등을
   이름 규칙 없이도 잡기 위함).
5. 헤더와 인증: `Authorization`·`Proxy-Authorization`·`Cookie`·`X-Api-Key` 류 헤더의 값(따옴표 안 포함), 어디에 있든 `Bearer`·`Basic` 뒤의 값.
6. 알려진 형태: PEM 개인 키 블록, JWT 꼴(`eyJ`로 시작하고 `.`으로 나뉜 세 덩어리), `AKIA`·`ASIA`+16자, `ghp_`·`gho_`·`ghs_`·`ghu_`·`ghr_`·`github_pat_`,
   `glpat-`, `sk-`(`sk-ant-` 포함)·`sk_live_`·`rk_live_`, `xox?-`, `AIza`+35자, `hf_`, `npm_`.

- 거부 목록이라 모든 비밀을 잡지 못한다(위치 인자의 비밀 `mysql -pVALUE`, 이름 없는 문자열, 인코딩한 값). 기록은 "가림을 적용했다"이지
  "비밀이 없다"가 아니다. 공유본에 넣지 않는 이유의 하나다(결정 10).
- **가리는 것과 원본으로 두는 것을 나눈다.** 가리는 것: 명령 글(인라인·첨부), 이벤트의 `text`, telemetry `summary`(OQ6). 원본으로 두는 것:
  결정 4의 거름을 통과한 스크립트 사본뿐이다. 그 거름은 이름 규칙·내용 가림 규칙·git 무시를 먼저 보며, 비밀 이름 파일의 복사 금지는 스크립트 규칙보다 앞선다.
- 비밀일 수 있는 이름의 파일(`.env`·`.env.*`, `*.pem`·`*.key`, `.netrc`·`.pgpass`, `id_rsa*`·`id_ed25519*`, `credentials*`, 이름에 `secret`)은
  변경 목록과 스크립트 목록에 경로·변경 종류·크기만 두고 해시하거나 복사하지 않는다(`hash: withheld`).
- 작업 디렉터리는 프로젝트 상대 경로만 남긴다(결정 2). `session`은 다른 이벤트와 같은 세션 파일 경로다(C1에 이미 있는 값).
- 출력(stdout·stderr)은 남기지 않는다. 출력은 aelix 세션 기록에 있고 `session`과 `tool_call_id`로 찾는다.
- telemetry `summary`도 같은 규칙으로 가린다(OQ6, 구현 PR의 필수 조건). 이미 쓴 telemetry 줄은 고쳐 쓰지 않는다(append-only).

### 6. 대기·취소·비용 상한

aelix는 두 hook을 기다리므로 관찰의 모든 비용이 bash 결과의 지연이다. 그래서 상한은 기다리는 쪽에서 강제한다.

| 단계 | 상한 | 넘으면 |
|---|---|---|
| 앞 상태(`tool_call`) | 항목 20,000개, 2초 | 그 관찰은 `changes.state=not_observed`. 항목 수 초과면 그 세션의 변경 관찰을 끈다 |
| 뒤 상태·해시·쓰기(`tool_result`) | 합 3초. 그 안에서 뒤 상태는 앞과 같은 항목 상한, 해시는 합 256 MiB와 남은 시간, journal 잠금은 남은 시간 동안 기다리지 않는 시도 | 뒤 상태 초과는 `not_observed`, 남은 파일은 `hash: over_budget`, 잠금을 못 얻으면 기록하지 않음 |
| 스크립트 읽기 | 8개, 파일당 1 MiB, 합 4 MiB(앞 상태의 2초 안) | 경로·크기만 |
| 명령 글 | 인라인 4 KiB, 첨부 1 MiB | 앞 1 MiB만 두고 `truncated: true`(잘린 사실을 기록에 남김, ADR-0006 §10) |
| 변경 목록 | 인라인 12 KiB, 첨부 10,000개 | 수만 정확(`items_complete=false`) |

- 한 bash 호출에 관찰이 더하는 지연은 많아야 앞 2초와 뒤 3초다. 위 측정으로는 파일 1만 개에서 두 번의 상태 보기가 약 100 ms다.
- **기다리는 쪽의 상한**: 작업은 스레드에서 하고 처리기는 `asyncio.wait_for`로 상한까지만 기다린다. 상한을 넘으면 취소 표지를 세우고 그 작업을 버린다.
  작업은 항목마다 표지를 보고 멈추며, 시스템 호출에서 멈춘 작업(멈춘 네트워크 마운트)은 끝날 때까지 남는다.
- **늦은 쓰기 금지**: 버린 작업은 아무것도 쓰지 않는다. 첨부 폴더를 만들기 직전과 journal 잠금 안에서 덧붙이기 직전에 표지를 다시 확인한다.
- **작업 수 제한**: 버린 작업이 아직 남아 있으면 새 관찰은 상태를 보지 않는다(`not_observed`, 사유 `busy`). 프로세스마다 남은 작업은 많아야 하나다.
- **취소**: `tool_result` 처리기는 기록 작업을 별도 task로 만들어 `asyncio.shield`로 보호하고 상한까지 기다린다. 처리기가 취소되면 결과를 바꾸지 않고
  취소를 그대로 올리며, 보호한 작업은 자기 상한 안에서 끝나거나 버려진다. aelix 0.1.0b2 기준이며 이후 판의 취소 동작은 시험으로 확인한다.
- **hook 오류**: 관찰의 처리기는 `error_mode="continue"`로 등록하고 취소 말고는 예외를 올리지 않는다. 관찰의 실패가 그 턴을 깨지 않는다.
- **남은 앞 상태**: `tool_result`가 오지 않으면(막힘·취소·포기) 그 호출의 앞 상태와 읽어 둔 스크립트 바이트는 `tool_execution_end`나 `agent_end`에서 버린다.
- **세션의 변경 관찰 끄기**: 항목 수 상한을 넘으면 그 세션에서 끄고 그 호출을 `observation_off`로 기록한다. 시간 초과는 그 관찰만 `not_observed`이고,
  첫 상태 보기(cold cache)를 뺀 연속 3번의 시간 초과일 때 끈다. 새 세션에서 다시 잰다. 끈 뒤에는 분석 실행 조건으로만 기록하므로 그 세션의
  다른 bash 호출의 변경은 보이지 않는다. 그 사각지대는 결정 7이 센다.
- 쓰기가 없다고 확신할 수 있는 명령의 상태를 보지 않는 판정은 구현이 더할 수 있다. 확신하지 못하면 본다.
- 상한의 수치는 이 규칙 아래에서 구현 PR의 측정 기록(파일 1천·1만·5만 개, 세 OS, cold·warm, 큰 단일 디렉터리)으로 확정한다(OQ4).

### 7. 읽기

- **reader**(`relations`): `observed_activity`를 아는 kind로 읽는다. `details.contract`가 `observed_activity/1`이 아니거나 필수 필드를 해석하지
  못하는 줄도 기록으로는 보이지만(목록·인용 대상) 관찰로 쓰지 않는다: 개수·출처 추적에서 빠지고 화면은 세부를 읽지 못했다고 적는다.
  상태 재생의 대상이 아니다(ADR-0006 §6 표의 "그 밖의 kind"). 철회·정정 대상이 아니다.
- **Workspace Timeline**: 행마다 종류 label `Observed activity`와 배지 `Outside the kit`(글로도 표시), 가린 명령 앞부분, 그 창에 바뀐 파일 수와
  그 가운데 설명되지 않은 수, 창 시각을 보인다. 설명은 "Inputs and environment not captured"다. 종류 필터와 종류별 목록 페이지에 든다.
  작성 주체는 관찰(`system`)로 보인다.
- **기록 페이지**(기존 관계 페이지): 가린 명령과 첨부 전문 링크, 작업 디렉터리, 창, 결과 상태, 스크립트(사본이면 코드 보기, `captured`·`stable`,
  사본이 없으면 그 사유), 바뀐 파일(데이터셋이면 그 페이지, 설명된 변경은 그 run), 언급한 파일, 그리고 남기지 않은 것(결정 9)을 고정 문장으로 적는다.
- **Evaluation의 Record coverage**: 관찰 수, 설명되지 않은 변경이 있는 관찰 수, 변경을 보지 못한 관찰 수(`not_observed`, 사유별)를 센다.
  변경 관찰을 끈 세션마다 끈 관찰과, 그 뒤 그 세션의 bash 호출 수(telemetry의 `session`·`tool`로 셈, telemetry가 빠졌으면 "적어도")를
  "changes not observed"로 보인다. telemetry의 `Tool usage`·`Possible workarounds`는 그대로다.
- **`analytics_status`**(인자 없는 현황): 최근 기록 10개 목록에서 관찰을 빼고 한 줄로 센다:
  `observed outside the kit: N (latest eK; M with changes no run recorded; changes not observed in S sessions) — part=records kind=observed_activity`.
  0이면 줄이 없다. telemetry 수를 말하던 `bypass_note`는 이 줄로 바꾼다(관찰 기록 전의 telemetry 수는 Evaluation에 남는다).
  [A2](../CONTRACTS.md#a2-제한된-맥락기록근거-조회-kit-0110)의 `part=records`는 `kind=observed_activity`로 관찰을 같은 cursor 규칙으로 읽는다.
- **맥락 블록**: 현재 세션의 관찰이 있을 때만 한 줄로 그 수와 최근 id, 입력·환경이 포착되지 않았으며 분석 단계는 `analytics_run`으로 하라는 안내,
  그 세션에서 변경 관찰을 껐으면 그 사실을 넣는다. 핵심 정보·열린 항목 목록에는 넣지 않는다.

### 8. 출처 추적(C18)

- 데이터 노드(경로·sha256)의 생산자 판정(`producer_state`, run 후보)은 바꾸지 않는다. 그와 따로, 같은 경로·sha256의 `unexplained`인
  `created`·`modified` 변경을 가진 관찰을 노드의 `observed_changes`에 더한다. 선은 새 종류 `changed_during`(관찰 → 데이터, `basis: unverified`,
  `confirmed: false`)이고 문장은 "Changed while this ran (not verified)" 류다. 지금 `basis: observed`는 run이 그 바이트를 썼다는 관측(`wrote` 선)이라
  쓰지 않는다. 선 이름도 입력 상태의 `changed`와 겹치지 않게 한다.
- **시간 방향**: 출발점이 어떤 run이 읽은 입력이면, 그 run이 시작하기 전에 창이 끝난 관찰만 후보다. 창이 그 run의 시작에 걸친 관찰은 `uncertain`으로
  따로 보이고, 그 뒤의 관찰은 그 입력의 출처가 아니다(같은 바이트가 나중에 다시 쓰였다는 것은 그 파일의 이력이지 출처가 아님). 시각은 시간대를 반영한
  시점으로 비교하며 run 후보의 규칙(ADR-0006 §4)과 같다. 지금 파일·`data:<경로>@<sha256>` 출발점은 그 바이트를 본 모든 관찰이 시간순 후보다.
- **run과 겹칠 때**: 같은 경로·바이트의 run 후보가 있으면 관찰 쪽은 "a run also recorded these bytes"로 낮춰 보인다(창 안에서 시작해 창 뒤에
  끝난 run의 출력 등).
- 관찰은 확정 연결로 오르지 않는다. 생산자 판정을 `resolved`로 만들거나 run 후보를 대신하지 않으며 `checks`의 확정 연결 수에 들지 않는다.
- 관찰 노드는 run처럼 한 단계다. 출처 방향은 관찰에서 멈추고 입력이 포착되지 않았다고 적는다. 스크립트는 노드가 아니라 관찰 노드의 값이다.
  사용처 방향은 `unexplained` 변경 파일과 그 관찰을 인용한 기록이다.
- 확인 줄(`checks`)에 `observed`(경로에서 만난 관찰의 수, 이름은 앞 20개)를 더한다. 다른 줄과 하나의 점수로 합치지 않는다.
- 해시가 없거나 설명이 `unknown`인 변경은 후보가 아니다. 지금 없는 파일(`Missing now`)의 데이터 노드에는 그 경로를 `deleted`로 본 마지막 관찰을
  덧붙일 수 있다.
- 기록이 관찰을 인용한 `event` 참조는 지금 규칙대로 판정한다. 정확한 대상을 찾았다는 확정 연결의 뜻(ADR-0006 §3)을 바꾸지 않는다. 화면은
  대상 종류로 그것이 관찰임을 보이고 `observed` 확인 줄이 따로 센다. 근거가 검증됐다는 뜻이 아니다.

### 9. 주장하지 않는 것

- **입력**: 무엇을 읽었는지. `mentioned`는 명령 글의 이름일 뿐이다.
- **환경**: 인터프리터·Python·패키지·`uv.lock`. 프로젝트 환경을 썼는지도 모른다.
- **재현성**: 재실행 대상이 아니고 재실행 비교가 없다. `analytics_run`의 `rerun_of`는 관찰을 받지 않는다.
- **실행한 바이트**: 사본·해시는 시작(또는 끝)의 바이트다. `stable: false`면 모르고, `stable: true`도 보증이 아니다(창 안에서 같은 바이트로 되돌림,
  다른 이름 해석). 다른 확장이 바꾼 명령(`changed_after_start`), 풀지 못한 이름, heredoc·`-c` 코드는 명령 글에만 있다.
- **인과**: 바뀐 파일은 그 창에 바뀐 것이며 그 명령이 썼다는 증거가 아니다.
- **완전성**: 관찰 범위 밖(관리 영역·`scripts/`·프로젝트 밖), 상한을 넘은 부분, 변경 관찰을 끈 세션의 Python이 아닌 명령, 도구 호출 뒤의 백그라운드
  쓰기, 크기·mtime·정체성을 모두 보존한 바꿔 쓰기, `tool_result`가 오지 않은 호출(막힘·취소·포기), 버린 작업의 관찰.
- **출력·검사·검토**: 출력 내용, 분석 방법 검사, 결론 검토. run의 `execution_status`·`recording_status` 축도 없다. `result.is_error`는 aelix가 준
  도구 결과일 뿐이다.

### 10. 공유본

- read·evidence 두 프로필 모두 관찰을 담지 않는다(OQ1). `PROJECT_KINDS` 밖이라는 지금의 기본 제외 그대로이며 Workflow feedback·Tool usage와
  같은 내부 작업 기록으로 둔다([공유 build 설계](../design/PUBLICATION-BUILD.md) §5.3의 OQ3). 가림이 거부 목록이라 명령 글을 공유해도 된다고 말할 수 없다.
- 포함한 기록이 관찰을 인용하면 그 참조는 범위 밖 대상의 기존 표시(`not_included`, 종류·상태만, 이름·명령 없음)다. 공유본의 데이터 노드는
  관찰 후보를 보이지 않는다.
- **[ADR-0014](0014-publication-record-contract.md) 결정 2의 개정**(OQ8): 내용 기준(`content_seq`)에서 안전하게 뺄 수 있는 닫힌 kind 집합에
  `observed_activity`를 더한다. 그 결정의 다른 규칙은 그대로다: 알고 있는 schema·kind의 정상 줄만 빼며, `details.contract`를 해석하지 못하는 관찰 줄,
  `unsupported`·복구한 줄은 빼지 않는다. 그래서 관찰만 더해진 뒤의 다시 build는 내용 해시가 같고, 공유본의 "Records up to eN"이 공유본에 없는 관찰을
  가리키지 않는다. 투영·상태 재생에도 넣지 않는다. 이 개정의 구현과 시험(`publishing`의 내용 기준 집합, 같은 내용 반복 build)은 구현 PR이 한다.

### 11. #170의 알림·telemetry와의 관계

- 알림과 기록은 같은 `tool_result` 처리기가 함께 낸다. 알림은 기록을 결정 6의 상한(3초) 안에서 기다린다. 기록했으면 알림 끝에 관찰 id를
  덧붙이고(예: `observed as e57: command, scripts and files changed while it ran`), 상한·잠금·관찰 꺼짐으로 기록하지 못했으면 짧은 사유를 덧붙인다.
  알림은 계속 `analytics_run`을 권한다: 관찰은 run을 대신하지 않는다.
- **알림의 조건을 넓힌다**(OQ3): Python이 아닌 명령이라도 설명되지 않은 변경(`unexplained`·`unknown`)을 남겼으면 짧은 알림을 붙인다:
  "files changed while this ran and no run or import recorded them (observed as eN); record data with analytics_import or analytics_run". 그 명령이
  썼다고 말하지 않는다. #170의 수용 조건("다른 명령에는 붙지 않는다")을 바꾸며, PR #176 병합 뒤 #171의 구현 PR이 한다.
- 그 세션의 변경 관찰을 끈 호출의 알림은 그 사실을 한 번 알린다.
- telemetry의 형식·`python_via_bash`·`wrote_managed_path`·Workspace의 `Tool usage`·`Possible workarounds`는 그대로이고 `summary`만 가린다(OQ6).
  관찰은 telemetry를 쓰지 않는다. 결정 7의 사각지대 수만 telemetry를 읽는다. 분석 실행의 판정은 #170과 같은 함수를 쓴다.

### 12. 버전과 호환

- 이 kind의 첫 writer는 그 쓰기 전에 표식의 `min_writer_version`을 자기 버전으로 올린다(ADR-0006 §1의 3항, `Writer`의 `requires`). 사람이
  요청하지 않은 부수 쓰기가 floor를 올린다는 점이 이 kind의 특징이다.
- kit 번호(OQ5): 소유자 결정(2026-11-09)으로 0.32.1을 올리지 않으므로 floor 값은 0.32.1이다. 이는 ADR-0006 §1의 3항("규칙을 더하는 PR은 같은 PR에서
  `KIT_VERSION`을 올린다")의 예외다. 구현 전의 main 0.32.1 체크아웃은 이 kind를 모르면서 같은 번호를 달고 floor 0.32.1의 프로젝트에 쓸 수 있다.
  0.32.1은 게시·후보 기록 전이고 0.32.0은 게시하지 않으므로 게시된 kit 사이에 같은 번호·다른 규칙은 생기지 않는다. 구현 전 체크아웃을 막지 못하는 것은 한계다.
- 0.32.1 미만의 kit(게시된 것은 없다)은 관찰 줄을 `unsupported`(`unknown_kind`)로 보존·계수하고(ADR-0006 §1의 4항), floor가 오른 뒤에는 쓰기를
  `writer_too_old`로 거절한다. 구현 전의 0.32.1 체크아웃은 관찰 줄을 `unsupported`로 보이며 이 kind를 쓰지 않으므로 어긋난 줄이 생기지 않는다.
  그런 체크아웃이 만든 공유본은 관찰 줄을 내용으로 센다(ADR-0014 결정 2의 모르는 kind 규칙).
- `.analytics/observed/`는 run의 출력 비교와 입력 캡처가 이미 빼는 `.analytics/` 아래다. run·Workspace 렌더·전환의 동작은 바뀌지 않는다.
  새 템플릿의 `.gitignore`에 넣지 않는다: CONTEXT 버전처럼 프로젝트의 이력이다. 그래서 결정 4·5의 거름이 사본의 조건이다.
- schema(`event_2`의 kind와 details)·정상/음성 예제·[현재 계약](../CONTRACTS.md)·[용어집](../TERMINOLOGY-AND-LANGUAGE.md)은 구현 PR이 함께 바꾼다.

## 소유자 결정

모두 소유자 결정(2026-11-09, 권장안 채택 지시)이다.

| OQ | 질문 | 결정 |
|---|---|---|
| OQ1 | 공유본에 관찰을 담는가 | 담지 않는다(read·evidence 모두, 결정 10). evidence 프로필에 넣는 것은 필요해질 때 후속 결정으로 |
| OQ2 | write·edit 도구, 사람의 `!` 명령도 관찰하는가 | v1에서 하지 않는다(결정 1). write·edit는 후속 후보, `!` 명령은 사람의 셸을 감싸야 해서 하지 않는다 |
| OQ3 | Python이 아닌 명령이 설명되지 않은 변경을 남겼을 때도 알림을 붙이는가 | 붙인다(결정 11). "그 창에 바뀌었다"로 말하고 그 명령이 썼다고 하지 않는다. #170의 수용 조건을 바꾼다 |
| OQ4 | 큰 `data/`의 비용 상한 | 결정 6의 규칙(나열 중 항목 수 상한, 기다리는 쪽의 시간 상한, 항목 수 초과·연속 3번 시간 초과에서 세션의 변경 관찰 끄기, 사각지대 계수)을 따르고 수치는 구현 PR의 측정으로 확정한다 |
| OQ5 | ADR-0006 §1의 3항 예외: 번호를 올리지 않고 floor 0.32.1 | 받아들인다(결정 12). 구현 전 체크아웃을 막지 못하는 한계를 둔다 |
| OQ6 | telemetry `summary`의 가리지 않은 bash 명령 300자 | 같은 가림 규칙을 적용한다. 구현 PR의 필수 조건이다(결정 5) |
| OQ7 | 프로젝트 밖의 스크립트(skill의 `~/…/run.py` 등) | 파일 이름·바이트 수·sha256만 남긴다. 경로와 사본은 없다(결정 4). 가림 규칙에 걸리면 해시도 없다 |
| OQ8 | 관찰 줄이 공유본의 `content_seq`를 앞당기는가 | 앞당기지 않는다. ADR-0014 결정 2의 닫힌 집합에 `observed_activity`를 더하는 개정이다(결정 10) |

## 대안과 선택 이유

| 대안 | 검토 결과 |
|---|---|
| 알림만(#170, 권장안 (a)) | 소유자가 (b)를 함께 정했다. 이미 한 작업의 흔적이 남지 않는다 |
| 별도 가족 `analytics.observed_activity/1` 파일과 완료 이벤트(run 기록 방식) | 새 참조 type, 두 단계 커밋, reader의 파일 읽기가 는다. 관찰은 작은 요약과 몇 개의 첨부라 이벤트 한 줄로 충분하고, ADR-0006이 새 kind를 이 용도로 열어 두었다 |
| telemetry에 필드를 더함 | 보조 로그는 seq·reader·출처 추적·공유 규칙 밖이다. 사용량 측정과 출처 기록을 섞는다 |
| 관찰을 run으로 합성(`runs/<id>/`, 상태 `observed`) | run은 실행한 바이트·입력·환경·재실행을 약속한다(C2). 빈 칸의 run은 그 약속을 깨고 재실행과 확정 연결의 오해(Q4)를 만든다 |
| 모든 bash 호출을 기록 | `ls`·`git`·`grep`가 Timeline을 덮는다. 기록할 사실이 없는 호출이다 |
| `python_via_bash`일 때만 기록 | `curl -o data/…`·`cp`·`duckdb`로 바꾼 데이터를 놓친다 |
| `user_bash`의 kit 명령 판정으로 상태 보기를 생략 | `python -c 'import aelix_analytics; …'`처럼 run 없이 쓰는 줄을 놓친다. 생략은 kit CLI 하나인 줄로 좁힌다 |
| 기록을 `tool_result` 뒤로 미룸(결과를 먼저 돌려줌) | 뒤 상태를 결과 뒤에 보면 다음 명령의 쓰기가 창에 섞이고, 늦은 기록의 seq가 다음 호출의 기록 뒤로 간다. 상한 안에서 기다린다 |
| 입력 캡처(OS 추적, `sitecustomize`·`PYTHONPATH` 주입으로 capture hook을 심음) | 플랫폼 포트(ADR-0003의 네 포트) 밖의 OS별 기능이거나 사용자의 프로그램 동작을 바꾼다. 실행을 kit에 맞추는 길은 `analytics_run`이다 |
| 파일 시스템 감시(FSEvents·inotify·ReadDirectoryChangesW) | 상주 감시자, OS별 구현, 이벤트 합침·누락. 이식 경계 밖이다 |
| 디렉터리 mtime으로 하위 생략 | 제자리 수정을 놓친다 |
| 앞뒤 내용 전체 해시 | 큰 `data/`에서 호출마다 전체를 읽는다 |
| 바뀐 파일을 해시 캐시로 해시 | 캐시 키가 `(크기, mtime_ns)`라 정체성만 바뀐 교체에 옛 해시를 돌려준다 |
| 스크립트를 모두 복사 | git이 무시하는 데이터·비밀 파일·노트북 출력이 git이 추적하는 `.analytics/observed/`로 옮겨진다 |
| 출력 저장 | 데이터 행·개인 정보가 들고 크다. aelix 세션에 이미 있다 |
| 명령 글을 가리지 않음, 또는 해시만 | 자격 증명이 git이 추적하는 journal에 남는다 / 해시만으로는 무엇을 했는지 읽을 수 없다 |
| 관찰을 생산자 `resolved`나 확정 근거로, 또는 `basis: observed`로 | 창의 관찰은 인과가 아니다(Q4). `observed`는 run이 썼다는 관측으로 이미 쓰인다 |

## 결과와 영향

- bash로 만든 파일이 "생산자 없음"에서 "관찰된 활동 eN의 창에 바뀜(검증 안 됨)"으로 보인다. 발견은 그 관찰을 인용할 수 있고, 화면은 그것이 관찰임을 보인다.
- 비용: 상태를 보는 bash 호출마다 상태 보기 두 번(측정한 기계의 파일 1만 개에서 약 100 ms), 바뀐 파일의 해시, journal 한 줄. 결과 지연은 많아야
  앞 2초·뒤 3초다. 큰 `data/`에서는 그 세션의 변경 관찰이 꺼지고, 그 사각지대는 수로 보인다.
- kit이 쓰는 journal 줄이 늘고(세션당 수십) Timeline이 길어진다. 맥락 블록은 현재 세션의 한 줄만 는다. 공유본의 내용 기준은 관찰로 움직이지 않는다.
- 첫 관찰이 표식 floor를 0.32.1로 올린다. 가린 명령 글과 거름을 통과한 스크립트 사본이 git이 추적하는 프로젝트 이력(journal·`.analytics/observed/`)에 남는다.
- run과 관찰이 같은 상태 비교 모듈을 쓰게 되므로 run의 출력 비교도 그 이동의 회귀 시험을 받는다.
- 기존 run 입력 해시의 캐시 키 한계(정체성만 바뀐 교체)는 main에도 있으며 이 결정은 관찰의 해시만 고친다.

## 구현 순서와 확인할 것

둘 다 0.32.1에 넣는다. 기록 형식 PR이므로 각 PR은 구현 리뷰(독립 1회·Codex 1회)와 수정 diff 확인을 받고, 세 OS CI와
측정 기록(`docs/verification/`의 md+JSON)을 남긴다. CHANGELOG는 0.32.1 절에 적는다.

1. PR-1 쓰기와 최소 읽기: run과 관찰이 함께 쓰는 상태 비교 모듈(`tree_stat`·`rekeyed`·`diff_outputs` 이동, 나열 중 상한), 관찰의 판정·가림·첨부를
   stdlib 전용 kit 모듈에 두고 확장이 aelix 프로세스 안에서 로드한다(계약 A의 import 목록, `tests/test_layers.py`, [아키텍처](../ARCHITECTURE.md) §11을
   함께 고침). 확장의 `tool_call`·`tool_result` 처리기(`error_mode="continue"`), writer의 kind·operation·floor, schema·예제, reader의 kind 인식,
   `analytics_status`의 수 줄과 A2 조회, #170 알림의 id와 넓힌 조건(PR #176 뒤), telemetry `summary` 가림, ADR-0014 개정의 `content_seq` 집합.
2. PR-2 화면과 출처: Workspace Timeline·기록 페이지·Record coverage(사각지대 수 포함), 맥락 블록의 한 줄, C18의 `observed_changes`·`changed_during`
   선·시간 방향·`observed` 확인 줄.

확인할 것:

- 기록 조건: kit 밖 Python, 설명되지 않은 변경, kit CLI 하나(상태를 보지 않음), `python -c 'import aelix_analytics; open("data/…","w")…'`(상태를 보고
  변경으로 기록), `uv run analytics run x.py 2>&1 | tail -20`(분석 실행 아님), `pip install`·`python --version`(기록 없음), `ls`(기록 없음), 다른 확장이
  막은 호출, 읽기 전용·옛 형식 프로젝트(상태 보기·쓰기·floor 변경 없음), 프로젝트 밖 작업 디렉터리에서 프로젝트를 가리키지 않는 Python.
- 상태: 만들기·고치기·지우기, `cp -p`·`rsync -a`처럼 mtime을 보존한 교체(새 해시), 같은 바이트로의 교체(변경 아님), 링크 경계를 만들기·지우기
  (`ln -s` 외부 디렉터리가 `created` 수천 개가 되지 않음), 뒤 stat의 공유 위반(`unreadable`), 해시 중 변경(`unstable`), bash 안의
  `analytics run`·`analytics data import`로 설명된 변경, 창 안 다른 프로세스의 쓰기, journal 꼬리를 읽지 못함(`unknown`).
- 스크립트: 시작 때 있던 파일, heredoc으로 만든 파일(`captured: after`), 창 안에서 바뀐 파일(`stable: false`), 다른 처리기가 바꾼 명령,
  `cd sub && python task.py`와 따라갈 수 없는 `cd "$X"`, git이 무시하는 파일·`data/` 아래 `.sql`·비밀 이름 파일·비밀 내용 스크립트·`.ipynb`의 사본 거부,
  프로젝트 밖 스크립트(이름·바이트·sha256만), 같은 바이트의 사본 하나, 상한.
- 대기·취소: 큰 단일 디렉터리(정렬 전에 멈춤), 연속 시간 초과와 cold 첫 걷기, 항목 수 초과의 세션 끄기와 `observation_off` 기록, 멈춘 읽기(버린 작업이
  나중에 쓰지 않음, 다음 관찰은 `busy`), `tool_result` 처리 중 취소(결과 불변·늦은 쓰기 없음), 처리기 예외(턴이 깨지지 않음), 오지 않은
  `tool_result`(앞 상태를 버림), journal 잠금을 얻지 못함, 줄 예산을 넘는 입력(최소 details).
- 가림: 규칙마다 canary 값(시험 값의 이름은 value·marker·canary)이 명령 글·`text`·첨부·telemetry에 원문·해시로 남지 않음, 비밀 이름 파일·비밀
  내용 스크립트의 `withheld`, 모든 URL 질의 값. 거름을 통과한 스크립트 사본의 원본 보존은 이 검사의 대상이 아니다.
- 호환: 관찰 줄이 든 journal을 0.32.0 후보로 읽기(`unsupported` 계수와 다음 seq), floor 뒤의 `writer_too_old`, 구현 전 0.32.1 체크아웃의 쓰기,
  `analytics record`·SDK가 이 kind를 쓰지 못함.
- 읽기·출처: 후보가 더해져도 `producer_state`·확정 연결 수 불변, 오전 run의 입력에 오후 관찰이 후보로 붙지 않음, 걸친 창의 `uncertain`, run과 겹친
  바이트의 낮춤, `Missing now`의 삭제 관찰, 공유 build가 관찰을 담지 않고 명령 canary가 패키지에 없음, 관찰만 더한 뒤 다시 build한 내용 해시가 같음.
- 측정: 파일 1천·1만·5만 개와 큰 단일 디렉터리의 상태 보기·해시 비용, bash 호출마다 늘어난 지연(앞·뒤), 관찰 줄 크기를 세 OS(CI)에서.

이 PR의 문서 검사와 설계 리뷰는 runtime·세 OS·실제 모델의 통과 증거가 아니다.
