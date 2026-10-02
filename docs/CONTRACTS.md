# Aelix Analytics 계약 v1

Aelix Analytics가 현재 **약속하는 것과 약속하지 않는 것**의 전부입니다. 코드가 기준이며,
이 문서와 코드가 다르면 코드가 맞고 문서를 고칩니다. 스키마 이름에 버전(`/1`)이 붙습니다.

## A. aelix 확장 표면 — 쓰는 것 전부

| 표면 | 쓰임 |
|---|---|
| `setup(aelix)` | 단일 파일 `extension/aelix_lab.py`의 팩토리. 설치본에서는 같은 파일이 모듈 `aelix_analytics.extension`이고, aelix가 entry point `aelix.extensions: aelix-analytics = aelix_analytics.extension:setup`과 manifest `aelix_analytics/aelix-plugin.toml`로 찾습니다 |
| `register_flag("lab-project", type="str")` | 프로젝트 지정 |
| `register_tool` × 6 | `lab_status`, `lab_query`, `lab_import`, `lab_run`, `lab_note`, `lab_init` — 모두 `execution_mode="sequential"`, 스키마는 `pattern` 없는 평범한 JSON Schema, `prompt_snippet`·지침은 줄마다 200자 이하 |
| `register_command("lab")` | 사람 명령 |
| `on("before_agent_start")` | `BeforeAgentStartResult(system_prompt=기존 + 맥락 블록)` — 그 요청에만 적용, 누적되지 않음 |
| `on("tool_execution_start" / "tool_execution_end")` | 모든 도구 호출을 `.lab/telemetry.jsonl`에 기록 |
| `on("agent_end")` | 분리된 프로세스로 `report --if-changed` 시작 |
| `events.emit` / `events.on` | 채널 `aelix-analytics:instance`로 한 runtime에 사본이 하나만 설정되게 확인 |

쓰지 않는 것: manifest의 선언형 기여(`[contributes]`: 명령·도구·hook·MCP·테마 선언), provider·login·API adapter,
메시지 렌더러, descriptor·TUI 위젯, MCP, subprocess hook, `send_message`·`append_entry` 같은 세션 조작.
manifest는 설치본이 aelix에 자기를 밝히는 데만 씁니다: `id = "aelix-analytics"`, 시작할 때 로드(`on_startup_finished`),
사실인 capability 네 개(`shell_exec`·`fs_write`·`fs_read_user`·`net` — aelix가 이 확장에는 아무것도 강제하지 않습니다),
`license = "Apache-2.0"`(배포본의 라이선스, `LICENSE`). 체크아웃의 파일 로드
(`aelix -e`·symlink)는 manifest 없이 로드됩니다.
확장은 aelix 프로세스 안에서 stdlib와 aelix API, 그리고 kit의 `labkit/core.py`와 그 형제 `labkit/_platform.py`(둘 다 stdlib 전용)만 import합니다.
설치본에서는 그 앞에 package `aelix_analytics`(docstring만)와 `aelix_analytics._install`(kit 위치)이 import됩니다.
두 kit 파일은 kit 경로를 `__path__`로 갖는 합성 package의 submodule로 로드됩니다. package 이름에 kit 경로의 해시가 들어가고
로드가 끝나면 `sys.modules` 항목을 지우므로, 호스트 환경에 설치된 `labkit`을 가리거나 두 pack이 캐시를 공유하는 일이 없습니다.
kit은 모듈 이름으로 정합니다: `aelix_analytics.extension`으로 import된 설치본은 `importlib.resources`로 찾은 자기 kit을
쓰고 `AELIX_LAB_KIT`를 따르지 않습니다. 파일로 로드된 경우 `AELIX_LAB_KIT`가 있으면 그것, 파일 옆에 kit이 있으면(경로로
로드한 설치본) 그것, 아니면 체크아웃의 `kit/`입니다(`/lab where`가 어느 것인지 보여 줍니다).
aelix는 세션을 만들 때마다(시작, `/new`, `/resume`, `/fork`, `/reload`) 확장을 다시 설정합니다. 파일 로드는 그때마다 새
모듈이지만 설치본의 모듈은 프로세스에 한 번 import되므로, `setup()`이 활성 프로젝트·세션 같은 상태를 매번 처음부터
시작합니다(체크아웃 경로와 같은 결과). 그래서 설치본을 업그레이드하면 aelix를 다시 시작해야 새 코드가 로드됩니다.
한 aelix runtime에는 이 확장의 사본 하나만 설정됩니다. 먼저 설정된 사본이 등록을 마친 뒤 runtime의 event bus
(`aelix.events`, 채널 `aelix-analytics:instance`)에서 응답하고, 뒤의 사본은 아무것도 등록하지 않고 stderr에 알립니다.
이 확인이 없는 이전 버전의 사본은 응답하지 않으므로 감지되지 않습니다(G).
분석 라이브러리는 전부 프로젝트 uv 환경의 자식 프로세스에서 돕니다.

## B. 프로젝트 파일 규약

| 경로 | 규약 |
|---|---|
| `lab.json` | 있으면 프로젝트. 위치 탐색의 표식 |
| `CONTEXT.md` | 자유 형식 Markdown. `## 목표`(또는 Goal) 아래 첫 줄이 목표로 쓰임 |
| `journal.jsonl` | append-only. 한 줄에 이벤트 하나. 직접 고치지 않음 |
| `data/raw/<name>.parquet` | `lab_import`만 만든다. 같은 이름 재가져오기는 거절. 수정하지 않는 것이 규약(강제 아님) |
| `data/raw/<name>.meta.json` | 가져오기 기록 |
| `data/derived/**.parquet` | run이 쓴다 |
| `scripts/**.py` | 사람·모델이 편집하는 코드. run 입력·출력 비교에서 제외 |
| `runs/<id>/` | run이 만든 뒤 바꾸지 않는 것이 규약. `<id>` = `YYYYMMDD-HHMMSS-<script slug>[-n]` |
| `report/` | 파생물. 언제든 지우고 다시 만들 수 있음 |
| `.lab/` | `context/` 버전, `telemetry.jsonl`, `queries.jsonl`, `cache/`, `lock`(journal), `run.lock`(run 직렬화), `report.lock` |
| 신뢰 목록 | 프로젝트 **밖**의 `~/.aelix/agent/lab-projects.json`(`AELIX_LAB_TRUST_FILE`·`AELIX_CODING_AGENT_DIR`로 변경). init과 `/lab use`가 추가하며, 작업 디렉터리만으로 프로젝트가 활성화되려면 이 목록에 있어야 합니다 |

run의 **출력 비교 대상**은 프로젝트 루트에서 `.venv .lab runs report scripts .git __pycache__ …`와
`lab.json journal.jsonl CONTEXT.md pyproject.toml uv.lock .python-version .gitignore`를 뺀 모든 파일입니다.

## C. 레코드

### C1. 이벤트 `lab.event/1` (`journal.jsonl`)

| 필드 | 뜻 |
|---|---|
| `seq`, `id` | 1부터 증가하는 번호와 `e<seq>`. 프로젝트 파일 잠금 안에서 배정 |
| `ts` | 로컬 시간대 ISO 8601 (밀리초) |
| `kind` | `project` `import` `run` `context` · `finding` `decision` `assumption` `question` `todo` `note` `gap` `resolve` |
| `actor` | `agent` `human` `system` `script` `test` 등 |
| `text` | 최대 4,000자 |
| `refs` | `runs[]` `charts[]`(`script::chart-id`) `data[]` `events[]` `other[]` `context`(버전 파일명) `resolves`(이벤트 id) |
| `session` | aelix 세션 파일 경로(있을 때) |

- `refs`에 짧은 차트 id(`region-revenue`)를 주면 최근 버전의 `script.py::id`로 해석해 링크합니다.
  나머지 미해석 이름만 `other[]`에 남습니다. 목록은 이벤트당 **최대 24개**(`MAX_EVENT_REFS`) —
  파일 수백 개를 읽은 run도 저널 한 줄을 경로 덤프로 만들지 않습니다. 전체 목록은 `run.json`에 있습니다.
- **핵심 정보** = `finding`·`decision`·`assumption` 중 다른 이벤트의 `refs.resolves`가 가리키지 않는 것.
- **열린 항목** = `question`·`todo` 중 같은 조건. `resolve` 종류는 `resolves`가 필수입니다.

### C2. run `lab.run/1` (`runs/<id>/run.json`)

| 필드 | 뜻 |
|---|---|
| `status` | `running` → `ok` `error` `timeout` `cancelled`. 기록 중 프로세스가 사라지면 읽을 때 `incomplete`로 보임 |
| `title`, `actor`, `session`, `started_at`, `ended_at`, `duration_s` | |
| `script` | `{path, sha256, snapshot}` — 실행한 바이트의 sha256과 스냅샷 경로 |
| `modules[]` | 실행 중 import된 프로젝트 안 `.py`의 `{path, sha256, snapshot}` |
| `inputs[]` | `{path, sha256, bytes, via[], external}` — sha256은 런타임 읽기면 **읽은 시점**, 정적 스캔·선언이면 run 시작 시점의 내용. `via`: `polars.*` `duckdb.*` `lab.read` `lab.scan` `lab.sql` `scan` `declared` |
| `outputs[]` | `{path, change: created/modified/deleted, bytes, sha256, rows?, schema?, via: lab.save/diff, outside_data?}` |
| `charts[]` | C3 |
| `error` | `{type, message, line, summary}` — `line`은 스크립트 안의 마지막 traceback 줄 |
| `finalize_errors` | 기록 단계(출력 수집·모듈 스냅샷·환경·journal·note 등)에서 실패한 항목. 있어도 run.json은 기록됩니다 |
| `stdout`, `stderr` | 로그 파일 경로 |
| `env` | `{python, executable, platform, packages{}, uv_lock_sha256, kit_version, kit_sha256}` |
| `rerun_of`, `reproduction` | 재실행일 때. `reproduction = {of, verdict, inputs_same, charts{}, outputs{}}` |
| `warnings[]` | 기록 품질 경고 |

입력에서 제외: 이 run 디렉터리, `.venv/`, `.lab/`, 그리고 `via`가 `scan`뿐인데 이 run이 만들거나 바꾼 파일.

### C3. 차트 레코드 (run의 `charts[]`)

| 필드 | 뜻 |
|---|---|
| `id` | run 안에서 유일. 지정 id·제목의 ASCII slug, 알파벳이 없으면 `chart-<n>` |
| `kind` | `matplotlib` `plotly` `altair` `html` `file` |
| `via` | `lab.chart` `plt.show` `fig.show` `auto`(실행 끝에 열려 있던 그림) `file`(run 디렉터리에 직접 쓴 이미지·HTML) |
| `title`, `caption` | 제목이 없으면 그림에서 읽고, 그래도 없으면 id |
| `files` | `{png}` 또는 `{plotly}`(figure JSON) 또는 `{html}` 등, 프로젝트 상대 경로 |
| `data`, `data_rows`, `data_columns` | `lab.chart(data=…)`로 저장한 플롯 데이터 parquet |
| `line` | 스크립트에서 차트를 기록한 줄 |

차트의 정체성은 `run.script.path::chart.id`입니다.

### C4. 데이터셋 `lab.dataset/1` (`data/raw/<name>.meta.json`)

`name, path, sha256, bytes, rows, columns[{name, dtype}], description, imported_at,
source{path, sha256, bytes, format, modified_at}`.

### C5. 보조 로그

- `.lab/telemetry.jsonl`: `{ts, tool, ms, error, summary, flags[], session}` — `summary`는 bash 명령 앞 300자,
  write·edit·read 경로, `lab_*` 주요 인자. `flags`: `python_via_bash`, `wrote_managed_path`.
- `.lab/queries.jsonl`: `{ts, sql, rows, truncated, tables[], error?}`.
- `.lab/context/<YYYYMMDD-HHMMSS-ffffff>_<sha256 12자>.md`: CONTEXT.md 전체 사본.

## D. kit CLI

`uv run --project P python -m labkit --project P <command> …`. `PYTHONPATH`는 정확히 kit 하나입니다: 체크아웃의
`<pack>/kit`, 설치본의 `aelix_analytics/kit`. 사람은 `bin/lab`(체크아웃, POSIX)이나 설치본의 `analytics`(모든 플랫폼,
Windows에서는 `analytics.exe`)로 부릅니다. 두 진입점은 명령 이름 말고는 명령·인자·usage·종료 코드를 같게 지키고 kit의
출력을 바이트 그대로 넘깁니다. 측정한 차이는 F의 사람용 진입점 행에 있습니다.

- stdout 마지막 줄이 JSON 하나: `{"ok": true, …}` 또는 `{"ok": false, "error": {code, message, hint?, details?}}`.
  스크립트의 stdout·stderr는 run 동안 파일로 돌려지므로 섞이지 않습니다. 이 줄은 CLI가 직접 UTF-8로 인코딩해 씁니다.
  그래서 스크립트가 자기 stdout을 닫거나 떼어 내거나(`detach()`), 인코딩을 바꾸거나(`reconfigure()`), `write`를
  바꾸거나, 그 버퍼를 감싼 스트림이 run 뒤에 수거되며 버퍼를 닫아도 이 줄은 남고 그대로 읽힙니다. run 뒤에는 kit이
  만들어 둔 run의 stdout 버퍼로 한 번에 쓰고, 그 버퍼로 쓸 수 없으면 CLI 자신의 스트림으로, fd 1이 run을 시작할 때의
  파일을 더는 가리키지 않으면(스크립트가 fd 1에 직접 연 스트림이 수거되며 닫은 경우 등) kit이 따로 쥐고 있던
  디스크립터로 씁니다. Windows는 파이프나 콘솔끼리 구별하지 못하므로, 거기서는 fd 1이 닫혔거나 디스크 파일로 다시 열린
  경우만 알아챕니다.
  스크립트가 남긴 스레드가 run이 끝난 뒤 stdout에 쓰는 출력은, CLI가 결과를 쓰기 전에 쓴 것은 결과 줄 앞에, 그 뒤에
  쓴 것은 결과 줄 뒤에 오고 결과 줄 가운데에는 끼어들지 않습니다. run 동안 잡아 둔 `sys.stdout`(그것으로 만든 logging
  처리기, 그 버퍼를 감싼 스트림 등)으로 쓰든 `print()`로 쓰든, 파이프가 한 번에 받지 못할 만큼 결과 줄이 길어도
  그렇습니다. 다만 스레드가 그 버퍼를 감싸 직접 만든 스트림에 쓴 글은 그 스트림이 비워질 때 버퍼에 들어가므로, 결과보다
  먼저 썼어도 결과 뒤에 올 수 있습니다. 그 출력과 결과 줄이 run의 stdout 버퍼와 그 잠금을 함께 거치고, 결과를 쓸 때까지 run의 stdout 스트림이
  `sys.stdout`으로 남기 때문입니다(stdout이 파이프, 파일, POSIX 터미널일 때). 이 보장 밖에 있는 것: run의 스트림을
  거치지 않고 fd 1에 쓰는 출력(`os.write(1, …)`, C 코드, 스크립트가 fd 1에 직접 연 스트림), `-u`나 PYTHONUNBUFFERED로
  버퍼 없이 도는 kit, Windows 콘솔처럼 CLI의 stdout이 fd 1 위의 평범한 파일이 아닌 경우(결과는 CLI의 스트림으로
  씁니다), 스크립트가 run 안에서 stdout을 떼어 내거나 닫은 뒤의 `print()`(run 뒤에는 CLI의 스트림으로 갑니다),
  스크립트가 stdout 버퍼의 원시 파일을 떼어 내고(`sys.stdout.buffer.detach()`) 그 위에 만든 스트림(결과는 CLI의
  스트림으로 쓰므로 잠금을 나누지 않습니다), 스크립트가 남긴 시그널 처리기가 결과를 쓰는 도중에 출력하는 경우, 한
  프로세스에서 CLI를 여러 번 부르는 호스트(G).
  결과를 읽는 쪽이 읽기를 멈추면 결과와 그 스레드가 함께 기다립니다. 결과 줄까지 읽고 멈추어도, 그 스레드가 계속
  쓰는 한 CLI가 끝나지 않을 수 있습니다(이전 구현과 같습니다). 그 스레드가 결과 줄을 쓰는 사이에 버퍼를 먼저 잡거나,
  그 stdout으로 만든 logging 처리기를 logging이 끝날 때 비우면 기다림에 끝이 없습니다. kit이 끝날 때 이전 run의
  스트림을 비우는 일만 1초까지 기다립니다(F1). `run`이 준비 단계를 지나 출력을 run의 로그로 돌린 뒤에는(스크립트가 시작하지 못했어도)
  결과 줄 앞에 늘 빈 줄 하나를 쓰므로, 그 스레드의 출력이 줄바꿈으로 끝나지 않아도 결과와 한 줄에 붙지 않습니다. 그 밖의
  명령은 JSON 한 줄만 씁니다(`demo`는 예제 스크립트를 자식 프로세스로 돌립니다). 확장은 JSON 객체로 읽히는 마지막 줄을
  결과로 받으므로, 결과 뒤에 JSON 객체를 출력하는 스레드는 결과로 오인될 수 있습니다.
- 종료 코드: 0 성공, 1 처리된 오류, 2 사용법 오류, 3 run은 기록됐으나 `ok`가 아님, 130 중단. 스크립트가 남긴 데몬
  스레드가 run 뒤에도 stdout에 쓰고 있으면, 인터프리터가 끝날 때 결과 줄 뒤에서 SIGABRT(POSIX에서 134)로 끝날 수
  있습니다(이전 구현과 같습니다). `print()`로 쓰거나, `sys.stdout.buffer` 또는 그것을 감싸 만든 스트림으로 쓰는 스레드가
  그렇습니다. run 동안 잡아 둔 `sys.stdout` 자체로 쓰는 스레드는 그렇지 않습니다. 다만 CLI가 자기 stdout을 되찾는
  도중에 시그널 처리기가 예외를 올리면(두 번째 Ctrl+C 등) 그런 스레드도 SIGABRT로 끝날 수 있습니다.
- 명령: `init` `status` `context` `run` `import` `query` `note` `report [--if-changed]` `demo`.
- `run`이 코드 스냅샷이나 첫 기록(`running`)을 쓰지 못하면 만든 run 디렉터리를 지우고 `snapshot_failed`·`record_failed`로
  답합니다. hint는 원인에 따라 경로를 줄이라는 것(운영체제가 이름이나 경로가 너무 길다고 답했거나, 경로의 한 구성
  요소가 UTF-16 단위로 255개를 넘음: NTFS·APFS가 이름을 세는 단위이며 기본 다국어 평면 밖의 문자는 둘로 셉니다),
  디스크 공간, 제목·스크립트 이름·`LAB_ACTOR`·`LAB_SESSION`을 UTF-8로 쓸 수 있는 값으로 하라는 것, `runs/` 쓰기 권한 중 하나입니다. run 디렉터리를 만들기 전(잠금, `runs/` 자체)과 첫 기록 뒤(로그 열기)의 실패는
  이전과 같이 traceback으로 끝나며, 첫 기록 뒤라면 `running` 기록이 남아 `incomplete`로 보입니다. UTF-8로 쓸 수 없는
  문자를 담은 입력·출력 경로가 마지막 기록을 쓰지 못하게 하는 경우와, 그런 `LAB_SESSION`으로 바뀐 맥락을 동기화하는
  경우도 그렇습니다(G).
- `LAB_CANCEL_FILE`: 이 경로에 파일이 생기면 명령을 중단합니다(D1). 확장은 Windows에서만 넘기고, kit은 시작할 때 이
  변수를 환경에서 지워 스크립트의 자식 프로세스가 같은 파일을 보지 않게 합니다.

### D1. run의 중단

kit은 run 하나에 중단 처리기를 한 번 설치하고 run 기록을 쓴 뒤에 원래 처리기를 되돌립니다. 스크립트를 중단하는 곳은
준비 구간(입력 해시·프로젝트 트리 조회)과 스크립트 구간뿐이고, 구간마다 **한 번만** 중단합니다. 그 밖에서 도착한 취소는
기록만 하므로 run 기록을 쓰는 경로를 끊지 않습니다. 상태는 스크립트가 무엇으로 끝났는지가 아니라 처리기가 남긴 두
표시(취소가 왔다, 제한 시간이 지났다)로 정합니다.

| 상태 | 조건 |
|---|---|
| `timeout` | 스크립트를 아직 중단할 수 있을 때 제한 시간의 시그널이 오거나 그 시각이 지났다. 스크립트가 그 시그널을 가로채 멈추지 않았어도, 제한 시간이 지난 뒤에 취소가 왔어도 `timeout`. POSIX에서는 kit이 받은 SIGALRM을 어디서 왔든 제한 시간으로 칩니다(이전 구현과 같습니다): 다른 프로세스가 보냈거나 스크립트가 올렸거나 스크립트가 `signal.alarm`으로 kit의 타이머를 바꾼 경우도 `timeout`입니다. Windows에서는 kit의 타이머만 제한 시간입니다 |
| `cancelled` | 제한 시간 전에 취소가 왔다. 취소가 구간의 한 번을 쓴 뒤에는 그 구간의 제한 시간을 세지 않는다. 준비 구간의 취소는 스크립트를 실행하지 않고 `cancelled before the script started`로 기록 |
| `incomplete` | 기록 중 프로세스가 사라졌다(C2). 확장의 강제 종료가 여기에 해당 |

| | POSIX | Windows |
|---|---|---|
| 취소 | 확장이 `uv run` 자식에 SIGINT를 보내고 uv가 kit에 전달. SIGTERM도 취소 | 확장이 임시 디렉터리에 요청 파일을 만들고(`LAB_CANCEL_FILE`), kit의 감시 스레드가 주 스레드에 SIGINT를 올림(`signal.raise_signal`). 콘솔의 Ctrl+C(SIGINT)와 Ctrl+Break(SIGBREAK)도 취소로 받습니다: 설치본 `analytics`의 run에 의사 콘솔(ConPTY)과 고전 콘솔에서 보내 `cancelled`로 기록됐습니다(측정 — Ctrl+C는 의사 콘솔의 `\x03` 입력과 고전 콘솔에 붙은 도우미 프로세스의 `GenerateConsoleCtrlEvent`, Ctrl+Break는 도우미의 `GenerateConsoleCtrlEvent`와 의사 콘솔의 win32-input-mode 입력열). 사람이 보이는 콘솔 창에서 누른 키는 실행하지 않았습니다. Ctrl+Break가 uv를 먼저 끝내는 것은 G |
| 제한 시간 | `setitimer`와 SIGALRM. kit이 run 전체 동안 SIGALRM 처리기를 점유하고, 구간이 끝나면 그 구간이 켠 타이머를 끕니다 — 스크립트가 바꿔 놓은 주기 타이머도, kit의 중단을 잡은 스크립트가 그 뒤에 건 타이머도(이전 구현처럼 ITIMER_REAL을 끔). 구간이 타이머를 켜지 못했으면(`--timeout inf`) 호스트 프로세스의 타이머는 건드리지 않습니다(이전 구현은 껐습니다) | 타이머 스레드가 제한 시간을 표시한 뒤 SIGINT를 올림 |
| 강제 종료 | 확장이 10초 기다린 뒤 프로세스 그룹에 SIGKILL | 확장이 10초 기다린 뒤 `taskkill /F /T`. Windows에서는 SIGTERM을 가로챌 수 없어 강제 종료가 `cancelled`가 아니라 `incomplete`로 남습니다 |

스크립트가 이 시그널에 자기 처리기를 설치하면 kit 대신 그 처리기가 받습니다. 그 경우와 한 번만 중단하는 규칙의 비용,
Windows의 한계는 G절에 있습니다.

## E. 계산 규칙

| 대상 | 규칙 |
|---|---|
| 입력 기록 | 런타임 읽기는 **읽는 시점의 내용**으로 해시합니다. run이 스스로 만든 파일, 또는 다시 쓴 뒤 읽은 파일은 입력에서 제외합니다 |
| 입력 상태 | 기록 sha256 대비 지금 파일: `same` / `changed` / `missing` / `unknown`(sha 없음·디렉터리·URI). 지금 내용이 그 run 자신의 출력과 같으면 `same`(읽고 다시 쓰는 갱신 패턴). 크기·mtime이 같으면 캐시한 sha를 씀 |
| stale | 입력 중 `changed`·`missing`이 있거나, 입력 `(path, sha256)`을 만든 성공 run이 stale (전이, 깊이 20) |
| 코드 상태 | 원래 스크립트 파일 sha256 대비 `same` / `changed` / `missing` (재실행은 `n/a`) |
| 차트 정체성 | run 안에서는 차트 id가 정체성입니다. 같은 id로 다시 기록하면 나중 것이 이깁니다. 자동 캡처(`plt.show`·`fig.show`·남은 그림)는 이미 저장한 그림이 그대로면 건너뜁니다 |
| 차트 버전 | `script::id`별 성공 run 순서(재실행 run 제외). 같은 스크립트의 최신 성공 run에 없는 차트는 `retired` |
| view 이름 | 파일 stem을 `[a-z0-9_]`로. 숫자로 시작하면 `t_`. raw 우선, 겹치는 derived는 `_derived` |
| 재실행 판정 | 재실행이 `ok`가 아니면 `failed`. 차트: 둘 다 플롯 데이터면 프레임 동등성, plotly면 `data` JSON 동등성, PNG면 바이트. 출력: 바이트. 다르거나 사라지거나(`not written`) 새로 생기면 `mismatch`, 같은 것이 있으면 `match`, 비교할 것이 없으면 `inconclusive`. 입력 동일 여부는 따로 `inputs_same` |
| 읽기 전용 쿼리 | 한 문장이어야 하고 종류가 SELECT(DESCRIBE·SUMMARIZE·SHOW·PRAGMA 포함)여야 합니다. `EXPLAIN`은 안쪽이 SELECT일 때만, `EXPLAIN ANALYZE`는 거절. 연결은 `allowed_directories=[<project>/data/]` + `enable_external_access=false` + `lock_configuration=true`로 묶습니다 |
| SQL 계보 | DuckDB 파서(`json_serialize_sql`)로 참조 테이블을 찾고 CTE 이름은 그 범위 안에서만 제외합니다. 파서가 받지 않는 문장은 FROM·JOIN 뒤 이름만 맞춰 보는 대비책을 씁니다 |
| 프로젝트 활성화 | `/lab use`·flag·env는 명시적 선택입니다. 작업 디렉터리로만 찾은 프로젝트는 신뢰 목록에 있어야 활성화되고, 아니면 `project_untrusted`로 거절하며 맥락 주입·telemetry도 하지 않습니다 |

## F. 실행 환경과 지원 플랫폼

지원 플랫폼은 Linux·macOS·Windows입니다([ADR-0002](decisions/0002-python-support-and-platform-scope.md), 2026-09-21 채택).
이식 가능한 경계의 구현 방식은 [ADR-0003](decisions/0003-portable-boundaries-and-in-process-kit-loading.md),
배포본·진입점·설치 자원은 [ADR-0004](decisions/0004-minimal-distribution-and-entry-points.md)입니다.
아래 표는 **현재 코드가 실제로 하는 것**이며, 채택한 목표와 구별합니다.

| 항목 | 현재 코드 |
|---|---|
| 선언된 Python 하한 | 배포본(`pyproject.toml`의 `requires-python`, wheel의 `Requires-Python`)은 `>=3.11`입니다. 이 하한은 설치 환경에서 도는 것 — `analytics` 런처, aelix 확장, 확장이 로드하는 `core`·`_platform` — 의 것입니다. 개발 환경(dev dependency group, `[tool.uv.dependency-groups]`)과 분석 프로젝트 환경(`kit/labkit/template/pyproject.toml`)은 `>=3.12`이고, kit의 명령은 프로젝트 환경에서 돕니다. `uv.lock`의 고정 버전은 바뀌지 않았고 dev group은 3.11에서 sync되지 않습니다. 하한 판정([측정 기록](verification/2026-10-02-issue-8-windows-inventory.md)): `>=3.11`은 설치 환경에서 도는 것에 대해 성립합니다 — 세 플랫폼에서 CPython 3.11·3.12에 설치, CPython 3.11에서 aelix 0.1.0b2 통합, 표준 라이브러리만으로 aelix 프로세스가 로드하는 부분(3.11), 런처 시험(3.11), Windows의 3.11 설치본으로 콘솔 Ctrl+C·Ctrl+Break. 분석 프로젝트는 `>=3.12`로 남습니다. D1의 중단 보장은 kit이 도는 3.12 이상에서 측정했고, 3.11에서는 분석 라이브러리를 쓰지 않는 스크립트의 `run`이 실행되고 기록되는 것만 확인했습니다 |
| 실행으로 확인한 범위 | 이식 가능한 경계(PR-1)는 Ubuntu 24.04와 Windows Server 2025(GitHub Actions), macOS 26.6.2 arm64(개발 기기)에서 실행했습니다([측정 기록](verification/2026-09-24-issue-8-platforms.md)). 배포본(PR-2)도 같은 세 플랫폼에서 wheel을 빌드해 체크아웃 밖의 새 CPython 3.11·3.12 환경에 설치하고 `tests/test_packaging.py`(end-to-end 단계 포함)로 확인했습니다([측정 기록](verification/2026-09-30-issue-8-distribution.md)). Windows 인벤토리(링크·긴 경로·대소문자·POSIX를 가정하던 시험·콘솔·두 프로젝트 격리)는 저장소 CI(`.github/workflows/ci.yml`)가 Ubuntu 24.04·Windows Server 2025에서 PR마다, macOS 26에서 라벨로 실행했고 개발 기기 macOS에서도 실행했습니다(콘솔과 긴 경로의 시험은 Windows에서만 돕니다)([측정 기록](verification/2026-10-02-issue-8-windows-inventory.md)). 측정한 Windows 구성은 하나입니다: Windows Server 2025 Datacenter(GitHub 호스트 이미지 `windows-2025`, ImageOS `win25-vs2026` 20260925.250.1), build 26100 x64, 관리자 계정(High integrity는 조사 실행에서 읽음), Developer Mode 켬, LongPathsEnabled=1(긴 경로 시험은 0으로도). 클라이언트 판(같은 build의 Windows 11 24H2 포함), 표준 사용자 토큰, 기본 설정의 Windows는 실행하지 않았습니다. Windows에서 확인한 것, 선언한 한계, 실행하지 않은 것은 G절에 있습니다. 실행하지 않은 조합으로 지원을 넓혀 적지 않습니다 |
| 검증된 조합 | 마지막 측정([측정 기록](verification/2026-10-02-issue-8-windows-inventory.md), `4eb0955` 커밋): 저장소 CI의 Ubuntu 24.04 x64(CPython 3.12.3·3.11.15), Windows Server 2025 x64(3.12.10·3.11.9), macOS 26.6.2 arm64 호스트 러너(`macos-26`, 3.12.10·3.11.9)에서 저장소 시험(배포본 end-to-end 포함)·플랫폼 스모크·aelix 0.1.0b2 통합(3.12·3.11, 분석 라이브러리가 없는 격리 환경)·런처 시험(3.11), `actions/setup-python`의 CPython 3.11(Ubuntu 3.11.16, Windows·macOS 3.11.9)에서 aelix 프로세스가 로드하는 stdlib 부분. Windows는 여기에 설치본의 콘솔 시험(3.12·3.11 설치본)과 LongPathsEnabled=0의 긴 경로 시험을 더했습니다. 개발 기기 macOS 26.6.2 arm64: CPython 3.12.13(저장소 시험과 end-to-end·스모크·aelix 0.1.0b2 통합), 3.11.15(aelix 0.1.0b2 통합·런처 시험·stdlib 스모크), 3.13.13·3.14.5(저장소 시험, end-to-end 없이). 배포본은 설치 환경 CPython 3.11·3.12에서 `analytics` 런처, aelix 0.1.0b2의 확장 결합·로드·도구, aelix 없이 SDK로 기록한 run과 그 run의 실제 uv 재실행(`run --rerun-of`), 호출자가 둔 `PYTHONPATH` 대신 정확히 설치된 kit이 run에 가는 것을 세 플랫폼에서 실행했습니다(wheel·sdist의 구성원은 빌드한 파일로 확인). 3.11에서만 `python -m labkit`의 stdlib 명령과 분석 라이브러리를 쓰지 않는 스크립트의 run, pip 설치·제거를, 3.12에서만 sdist 설치, README의 symlink 경로(Windows에서는 `os.symlink`로 만든 파일 링크), README의 `uv tool install --python 3.12` 두 줄을 세 플랫폼에서 확인했습니다. `analytics open`은 POSIX에서 stand-in opener로, Windows 포트로는 `os.startfile`을 바꿔 넣어 실행했습니다(실제 opener는 G). 프로젝트 환경은 CPython 3.12 이상(시험이 확인) |
| 프로세스 경계 | 사람용 `analytics` 런처는 설치 환경에서 stdlib만으로 돌고, kit에서는 `_platform`만 로드합니다. stdlib·aelix API·`kit/labkit/core.py`와 `_platform`으로 제한되는 것은 **aelix 확장 프로세스**입니다. 확장은 `uv run --project` 자식 프로세스로 CLI를 띄웁니다(`extension/aelix_lab.py`의 `run_kit`). 그 CLI 프로세스는 `runner`·`data`·`report`를 자기 프로세스에서 import하고, `data.py`와 SDK가 그 안에서 duckdb·polars를 import합니다. 실행되는 스크립트도 같은 프로세스입니다 |
| 잠금·타임아웃·종료 | `kit/labkit/_platform.py`의 네 포트 뒤에 있습니다: `FileLocks`(journal·run·report 잠금), `AtomicFiles`(기록 교체), `ProcessControl`(자식 생성·생존 확인·중단 요청·강제 종료), `RunControl`(run 제한 시간과 취소, D1). 구현은 `core.PLATFORM`(모듈을 불러올 때 `_platform.detect()`가 한 번 고른 것)이 기본값이고 `Project(root)`가 그것을 씁니다. 시험과 호출자는 `Project(root, platform=…)`로 다른 구현을 넣습니다. 환경 변수나 기록 속 문자열로 구현을 고르지 않습니다. 호출부에는 `sys.platform` 분기가 없습니다. `ProcessControl`은 문서 열기(`open_document`: macOS `open`, 그 밖의 POSIX `xdg-open`, Windows `os.startfile`)와 프로그램 찾기(`find_program`: POSIX는 `shutil.which`, Windows는 작업 디렉터리와 상대 PATH 항목을 빼고 PATH에서만, 경로로 준 것도 `.exe`만)도 맡습니다. 찾은 프로그램은 절대 경로로, 심볼릭 링크를 따라가지 않고 씁니다(mise의 shim처럼 자기 이름으로 동작을 정하는 링크). POSIX 구현은 `fcntl.flock`, `os.replace`, SIGINT로 묻기, `setitimer`·SIGALRM, `start_new_session`과 `os.killpg(SIGKILL)`입니다. Windows 구현은 `msvcrt.locking`(경합이면 다시 시도), 다른 프로세스가 연 파일의 교체 거절(WinError 5·32·33)을 약 2초 동안 다시 시도하는 `os.replace`, 요청 파일과 `signal.raise_signal(SIGINT)`로 묻기, `threading.Timer`와 `raise_signal`로 제한 시간, `CREATE_NEW_PROCESS_GROUP`과 `taskkill /F /T`입니다. `taskkill`은 시스템 디렉터리(`GetSystemDirectoryW`, 그 호출이 실패하면 `%SystemRoot%\System32`)의 `taskkill.exe`를 절대 경로로 실행합니다 — 이름만 주면 CreateProcess가 호출자의 작업 디렉터리(aelix의 작업 디렉터리, 흔히 프로젝트)를 먼저 찾습니다. 콘솔 이벤트(`CTRL_BREAK_EVENT`)와 `_thread.interrupt_main`은 쓰지 않습니다 — 측정에서 앞의 것은 `uv`를 곧바로 끝내 그 자식을 기록 없이 남겼고, 뒤의 것은 잠든 주 스레드를 깨우지 못했습니다 |
| 생존 확인 | `running` 기록의 pid가 pid일 수 없는 값(0, 음수, bool, 플랫폼 범위 밖)이면 살아 있지 않습니다. 다른 사용자의 프로세스가 그 id를 가지면 재사용으로 보고 살아 있지 않다고 답합니다. 운영체제가 답하지 않으면 살아 있다고 보아 기록을 낮추지 않습니다. 같은 사용자의 프로세스가 재사용한 pid는 구별하지 못합니다 |
| 파일명 — 거절 | Windows 예약 장치명(`CON` `PRN` `AUX` `NUL` `CONIN$` `CONOUT$`, 숫자 1–9와 위 첨자 ¹²³이 붙은 `COM`·`LPT` — 첫 점 앞부분을 끝 공백을 빼고 대소문자 없이 비교)은 **사람이나 에이전트가 새 이름을 정하는 두 지점에서만** 거절합니다: `lab_import --name`과 `lab_run`이 만드는 `scripts/<name>.py`. 이미 있는 이름은 거절하지 않습니다 |
| 파일명 — 스크립트 경로 | run은 어느 플랫폼에서나 같은 뜻으로 읽히는 프로젝트 상대 경로만 기록합니다. 루트(`/`·`\`)나 드라이브(한 글자 뒤 콜론: `C:`, `é:`)로 시작하는 경로, 첫 구성 요소가 `~`로 시작하는 경로, `/`와 `\`로 나눈 구성 요소가 비었거나 `.`·`..`인 경로, 관리 디렉터리(`.venv` `.lab` `runs` `report`) 안의 경로 — `\` 구분자와 대소문자 변형 포함 — 는 기록하기 전에 `script_location`으로 거절합니다. 이전 기록을 재실행할 때도 같은 규칙을 적용하고, 링크를 따라간 실제 위치가 프로젝트 안이며 관리 디렉터리 밖인지 확인합니다 |
| 파일명 — run 안 | run 안의 `lab.save`와 스크립트가 직접 쓰는 경로는 거절하지 않습니다. 보존된 스크립트는 출력 파일이 지워진 뒤에도 다시 실행돼야 하기 때문입니다. 대신 run의 `warnings`에 남깁니다 — 출력과 차트 파일의 **모든 경로 구성 요소**를 검사하므로 `data/derived/con/value.parquet`도 걸립니다. 이 경고는 다른 경고를 가리지 않도록 묶어서 마지막에 둡니다 |
| 파일명 — 차트 | kit이 저장하는 차트 파일(`lab.chart`의 이미지·plotly JSON·플롯 데이터)만 이식 가능한 이름으로 바꿉니다(`con` → `con_.png`). 정체성 `script::id`는 생성된 id까지 그대로입니다. 이 대응은 단사입니다 — 차트 id는 slug이라 `_`를 포함할 수 없으므로 `con-file` 같은 보통 id와 충돌하지 않습니다. 스크립트가 차트 id 본래 이름에 쓴 파일(차트 `con`의 `charts/con.png`, 대소문자를 구별하지 않는 파일 시스템에서는 `charts/CON.png`도)은 이전 구현이 그 차트를 쓰거나 덮어쓴 파일이므로 따로 차트로 기록하지도 경고하지도 않고, 그 차트가 버려지면 함께 지웁니다. 그 밖에 스크립트가 run 디렉터리에 직접 쓴 파일(`register_loose_files`)은 스크립트가 정한 이름 그대로 차트가 되며 위 경고로만 알립니다 |
| 경로·인코딩 | 리포트의 절대 경로 링크는 기록한 플랫폼 기준으로 해석한 `file://` URL입니다(`C:\…` → `file:///C:/…`, `\\server\share\…` → `file://server/share/…`). 그래서 POSIX에서 이름이 `X:\`나 `\\`로 시작하는 프로젝트 파일은 그 Windows 경로로 링크되거나, 공유 이름이 없으면 링크되지 않습니다. CLI는 인자를 읽기 전에 표준 스트림을 UTF-8(`errors='replace'`)로 고정하고, 텍스트를 읽는 파일·자식 프로세스 호출은 인코딩을 명시합니다. run 로그도 UTF-8이며 인코딩할 수 없는 문자(짝 없는 surrogate)는 `?`로 씁니다. run 동안 stdout·stderr는 파일 기술자와 Python 스트림을 함께 바꾸고, 출력은 이전처럼 블록 단위로 버퍼링됩니다. 이렇게 해서 콘솔 전용 스트림이 파일에 쓰지 못하는 문제를 피합니다. Windows에서는 설치본 `analytics`를 의사 콘솔(ConPTY)에서 실행해 확인했습니다(측정): run의 stdout 로그에 스크립트의 한국어 출력이 남았고, 콘솔 코드 페이지 437과 949에서 한국어 제목이 콘솔에 그대로 보였습니다. 이것은 Python의 콘솔 경로로 쓴 kit의 출력(결과 줄의 한국어 제목)에 대한 결과입니다. 런처의 글도 같은 경로로 쓰인다고 봅니다(코드에서 읽음, 실행하지 않았습니다). 콘솔 파일 기술자에 바이트를 직접 쓰는 출력과, 출력 파이프가 없는 고전 콘솔의 글 순서·인코딩은 실행하지 않았습니다(`not_run`) |
| 사람용 진입점 | 체크아웃의 `bin/lab`(POSIX 셸)과 설치본의 `analytics`(stdlib Python 런처, 모든 플랫폼; Windows에서는 `analytics.exe`)입니다. 설치본의 이름은 제품 명칭의 CLI 이름이고, `bin/lab`은 명칭 전환(#9)까지 옛 이름으로 남으며 wheel로 설치하지 않습니다. 하위 명령(`note`·`report` 등)은 둘 다 아직 지금의 이름입니다. 둘은 명령 이름 말고는 같은 명령·인자·usage·종료 코드를 지키고 kit의 출력을 바이트 그대로 넘기며, POSIX에서 같은 stand-in uv로 동등성을 시험합니다. 런처는 자식을 같은 process group·콘솔에서 표준 스트림을 물려준 채 기다리고 SIGINT·SIGBREAK를 기록만 하므로, 터미널의 Ctrl+C를 런처는 다시 보내지 않고 kit의 기록을 기다려 kit의 종료 코드로 끝납니다. uv는 자기 표준 입력이 터미널이 아니면 200 ms 뒤 kit에 한 번 더 보냅니다(`bin/lab`에서도 같습니다). 런처가 무시된 SIGINT를 물려받으면(셸의 백그라운드 작업) 그대로 두어 kit도 무시한 채 시작합니다. 자식을 시작하는 순간에 온 Ctrl+C는 자식이 뜬 뒤 한 번 더 보냅니다(POSIX). 그 순간 터미널의 Ctrl+C를 이미 받은 자식에게는 두 번 갑니다. Windows에서는 다시 보내지 않습니다 — 한 프로세스에 SIGINT를 보낼 방법이 없고, 시험이 이를 고정합니다. 같은 콘솔의 자식은 콘솔의 Ctrl+C를 직접 받습니다. Windows 콘솔의 Ctrl+C도 런처가 다시 보내지 않고 kit의 기록을 기다려 kit의 종료 코드로 끝납니다(측정: 의사 콘솔과 고전 콘솔에서 런처가 `cancelled` 기록 뒤 3으로 끝났고 남은 프로세스가 없었습니다. Ctrl+Break는 G). 두 번 오더라도 kit은 run을 구간마다 한 번만 중단합니다(D1). 런처와 `bin/lab`의 차이: 런처만 받은 SIGTERM은 kit의 `run`에만 전하고(run이 `cancelled`로 기록), 그 밖의 단계(init·sync·status·context·import·query·note·report·demo와 `analytics open`의 문서 열기)는 끝까지 두고 다음 단계를 시작하지 않은 채 143으로 끝납니다(`bin/lab`의 셸은 그 자식을 남기고 곧바로 끝납니다). 이 SIGTERM은 POSIX의 것입니다 — Windows에서는 다른 프로세스가 처리기에 SIGTERM을 보낼 수 없고 `os.kill`·`taskkill /F`가 런처를 곧바로 끝냅니다(강제 종료, D1). 런처가 Ctrl+C나 SIGTERM을 기록한 뒤에는 다음 단계를 — `analytics open`의 문서 열기도 — 시작하지 않습니다. 단계를 시작하는 그 짧은 순간 온 신호는 그 단계를 막지 못할 수 있습니다. 그러면 Ctrl+C는 그 단계에 다시 보내고 SIGTERM은 kit의 `run`에만 보내며, 문서를 여는 프로그램에는 어느 것도 보내지 않으므로 리포트가 열릴 수 있습니다. uv가 없으면 런처는 자기 문구로 127입니다(`bin/lab`은 셸의 오류로 끝나며, macOS의 sh는 없는 경로에 1). 런처는 macOS에서만 `open`을 씁니다(`bin/lab`은 `open`이 있으면 어디서나). dash로 도는 `bin/lab`은 Ctrl+C 뒤 SIGINT로 끝납니다. 측정한 경계 사례의 차이: 빈 첫 인자(`lab ""`)는 `bin/lab`에서 status, 런처에서 usage와 2입니다. 실행할 수 없는 uv(디렉터리이거나 실행 권한이 없는 파일인 `AELIX_LAB_UV`, PATH에서 실행 권한이 없는 `uv` 파일)는 `bin/lab`에서 셸의 오류와 셸·플랫폼에 따른 종료 코드(126·127·1), 런처에서 uv를 찾지 못했다는 문구와 127입니다. 찾았지만 시작할 수 없는 uv(없는 인터프리터를 가리키는 스크립트)는 `bin/lab`에서 셸의 오류와 126(macOS의 sh)·127(dash), 런처에서 시작할 수 없다는 문구와 127입니다. 문서를 여는 프로그램이 없으면 둘 다 127이고, 런처는 자기 문구를, `bin/lab`은 셸의 오류를 냅니다. 작업 디렉터리가 지워졌으면 런처는 읽을 수 없다는 문구와 1로 끝나고, `bin/lab`은 macOS의 sh에서 셸의 오류와 1로 끝나지만 dash에서는 프로젝트를 찾는 반복이 끝나지 않습니다(main의 `bin/lab`과 같습니다). `AELIX_LAB_UV`의 `~`와 끝의 `/`는 런처만 풀어 uv를 실행합니다(`bin/lab`은 dash 127, macOS의 sh 1). 런처 자신의 글을 닫힌 파이프에 쓰면 `bin/lab`은 141, 런처는 `BrokenPipeError`와 120이고, 표준 출력을 닫은 채(`>&-`) `--help`를 부르면 `bin/lab`은 2, 런처는 traceback과 1입니다. symlink로 들어온 작업 디렉터리에서 `init ../DIR`은 `bin/lab`에서 kit이 프로젝트를 만든 뒤 실패하고(셸에 따라 1이나 2), 런처에서는 끝까지 진행합니다. 런처 자신의 글(usage, 안내)은 로캘과 상관없이 UTF-8 바이트입니다. 그 안의 경로·값은 Python의 파일 시스템 인코딩이 글자로 읽은 부분을 그 글자의 UTF-8로, 읽지 못한 바이트를 받은 그대로 씁니다. 그 인코딩이 UTF-8이면(macOS; Linux의 UTF-8 로캘과, Python이 기본으로 UTF-8 모드로 도는 C 로캘) 모든 경로·값이 받은 바이트 그대로이고, 그 인코딩이 Latin-1·EUC-KR 같은 것이면(그런 로캘의 Linux) UTF-8인 이름도 바뀝니다(`bin/lab`은 늘 받은 바이트). 런처는 `AELIX_LAB_KIT`를 따르지 않고 자기 kit을 씁니다 |
| 배포 형태 | wheel 하나를 hatchling이 체크아웃에서 만듭니다(`uv build`): package `aelix_analytics` — 런처 `cli.py`, kit 위치 `_install.py`, manifest, 확장 `extension.py`(체크아웃의 `extension/aelix_lab.py`) — 와 package data `aelix_analytics/kit/labkit`(템플릿·assets·데모 스크립트 포함). 의존성은 없고, 분석 라이브러리는 프로젝트 환경에만 설치됩니다. aelix 연동은 이 wheel을 aelix의 환경에 설치하는 것이며 extra는 없습니다. 개발 환경은 `package = false`로 프로젝트를 설치하지 않습니다. 라이선스는 Apache-2.0입니다(`LICENSE`; wheel의 `License-Expression`과 manifest의 `license`). 배포 이름은 첫 공개 전에 정할 사항이고, 이 wheel은 어디에도 게시하지 않았습니다 |

### F1. 이전 구현(`48b79cc`)과 달라진 POSIX 동작

이식 가능한 경계를 적용하면서 POSIX에서도 다음이 바뀌었습니다. 나머지 POSIX 동작 — 출력 버퍼링, SIGINT로 묻기 — 는
이전과 같습니다.

- 새 dataset·스크립트 이름의 예약 장치명과, 다른 플랫폼에서 다르게 읽히는 스크립트 경로를 거절합니다(F 표). 이전
  기록의 재실행도 같은 경로 규칙을 따르며, 프로젝트 밖으로 나가는 링크나 `runs/`로 가는 링크를 거친 재실행을 거절합니다.
- 리포트의 절대 경로 링크가 `file://` URL입니다. 예약 id의 차트 파일은 `_`를 붙인 이름으로 저장하고, 출력의 예약
  이름은 경고합니다.
- run의 중단(D1): 준비 구간의 취소도 `cancelled`로 기록합니다. 구간마다 한 번만 중단합니다. 제한 시간의 시그널을
  가로챈 스크립트와, 제한 시간이 지난 뒤 취소가 온 run은 `timeout`입니다. kit이 run 전체 동안 SIGALRM을 점유합니다.
- `snapshot_failed`·`record_failed`(D): 첫 기록을 UTF-8로 쓸 수 없을 때(제목·스크립트 이름·`LAB_ACTOR`·`LAB_SESSION`에
  UTF-8이 아닌 바이트)도 run 디렉터리를 지우고 답합니다. 스크립트가 `sys.stdout`이나 `sys.__stdout__`을 닫거나 떼어
  내거나 인코딩을 바꾸거나, 그 `write`를 바꿔도 CLI의 JSON 줄이 남고 그대로 읽힙니다(D). 이전 구현은 그 줄을 잃거나
  깨뜨렸습니다.
- 홈이 없는 `~user/…` 경로(`lab.path`, `lab.chart`의 파일, 선언한 입력, 정적 입력 스캔, `lab import`와 확장의
  `lab_import`의 원본)는 이전처럼 `RuntimeError`로 끝나거나 기록에서 빠지지 않고 적힌 그대로의 경로로 읽습니다(원본은
  작업 디렉터리 기준). 이 kit 자신의 경로에서 `~`로 시작하는 첫 구성 요소는 그것이 가리키는 홈 디렉터리가 실제로
  있을 때만 펼칩니다. Windows의 Python은 USERNAME이 사용자 프로필 디렉터리의 이름과 같으면 없는 사용자에게도 그 옆의
  홈을 추측하므로(`~$budget.xlsx` → `C:\Users\$budget.xlsx`), 이전에는 Windows에서 그런 경로를 `C:\Users` 아래의 없는
  경로로 읽었습니다(다르면 POSIX처럼 `RuntimeError`였습니다). 이제 kit 자신의 경로는 모든 플랫폼에서 같은 규칙을
  따릅니다. 다만 어떤 이름에 홈이 있는지는 플랫폼마다 다릅니다(`~Public`은 Windows에만 있습니다). 새 run의 스크립트
  경로로는 거절합니다(F 표).
  스크립트가 Polars·DuckDB로 읽어 기록되는 입력은 kit의 규칙이 아니라 그 라이브러리가 `~`를 푸는 방식대로 기록합니다.
  Polars는 `os.path.expanduser`로 펼치므로 POSIX에서는 없는 사용자의 `~이름`을 적힌 그대로 기록하고(이전에는 기록에서
  빠졌습니다), Windows에서는 위의 추측한 홈을 엽니다. DuckDB는 `~` 자리에 자기 홈 디렉터리를 넣고 나머지를 그대로
  붙입니다. kit은 그 홈을 HOME(Windows에서는 USERPROFILE)으로 보고 `~/x.csv`를 그 홈의 파일로 기록하므로, 스크립트가
  DuckDB의 `home_directory`를 바꾸었거나 HOME이 없으면 DuckDB가 연 파일과 다를 수 있습니다. `~이름…`(DuckDB에서는 홈
  디렉터리 경로 뒤에 `이름…`을 붙인 것)은 기록하지 않습니다. 이전 구현은 여기서 아무것도 기록하지 않거나, 그 사용자의
  홈이 있으면 DuckDB가 읽지 않은 그 홈의 파일을 기록했습니다.
- 정적 입력 스캔: 이런 문자열 하나, 또는 따옴표로 감싼 데이터 파일 이름을 담은 SQL 문자열 하나(`"SELECT * FROM
  'data/raw/sales.parquet'"`) 때문에 스캔이 찾은 입력을 모두 잃던 것을 고쳤습니다. 뒤의 것은 kit의 첫 구현부터 있던
  결함입니다. 이제 문자열마다 따로 실패하고, 문자열 안에 따옴표로 감싼 데이터 파일 이름(SQL 문자열의 것이든,
  메시지나 docstring이 인용한 것이든)과, 적힌 그대로 프로젝트 안에 있는 `~`로 시작하는 이름(`~$budget.xlsx`)도 입력으로
  기록합니다. 이전에는 그런 스크립트의 스캔 입력이 하나도 기록되지 않았습니다. 그래서 이전 구현의 그런 기록을 재실행하면
  읽는 파일이 바뀌지 않았어도 확장은 `inputs differ`, 리포트는 `입력 다름`을 보입니다(판정은 같습니다). 기록에 담을 수 없는
  이름(G의 UTF-8이 아닌 이름)은 스캔이 입력으로 넣지 않고, 같은 glob이 찾은 다른 파일은 기록합니다.
- 예약 id의 차트가 버려지면(같은 id로 다시 기록, 자동 캡처 뒤 이름 붙이기) 그 id 본래 이름의 파일
  (`charts/aux.png`)도 이전 구현처럼 지웁니다. 대소문자를 구별하지 않는 파일 시스템에서는 `charts/AUX.png`도 같은 파일로 봅니다.
  그래서 그런 파일 시스템에서 스크립트가 `charts/AUX.png`를 먼저 쓰고 id `aux` 차트를 기록하면, 이전 구현은 한 파일을
  차트 두 개(`aux`, `aux-2`)로 기록했지만 지금은 하나로 기록합니다. 이전 구현의 그런 기록을 재실행하면 `aux-2`가 없다는
  불일치가 나옵니다. 스크립트가 그 이름에 쓴 파일은, 차트와 다른 그림이어도 차트로 기록하지도 경고하지도 않고
  `charts/`에 남습니다(F 표의 본래 이름 규칙).
- 인코딩: CLI는 인자를 읽기 전에 UTF-8로 고정합니다. 짝 없는 surrogate는 이전에 C·UTF-8 로캘에서 stdout에는 바이트
  그대로, stderr에는 `\udcXX` 이스케이프로 쓰였고 다른 UTF-8 로캘에서는 run이 실패했습니다. 지금은 둘 다 `?`이고 run은
  `ok`입니다. 이전 run들의 출력 스트림은 인터프리터가 끝날 때도 비우되, 1초까지만 기다리고 그때까지 쓰지 못한 글은
  잃습니다. 스크립트가 남긴 스레드가 그 버퍼를 쥔 채 막혀 있어도, 읽는 쪽이 늦어도 그렇습니다(이전 구현은 읽는 쪽을
  끝까지 기다렸습니다). 그 기다림에 쓸 스레드를 시작할 수 없으면 비우지 않습니다. run 뒤의 CLI 결과는 새 줄에서
  시작합니다(D). 스크립트가 남긴 데몬 스레드가 run 동안 잡아 둔 `sys.stdout` 자체로 계속 쓰고 있으면, 인터프리터가
  끝날 때 SIGABRT로 끝나지 않습니다(이전 구현은 그렇게 끝날 수 있었습니다). `print()`로 쓰거나, `sys.stdout.buffer`
  또는 그것을 감싼 스트림으로 쓰는 스레드는 이전처럼 SIGABRT로 끝날 수 있습니다(D).
- 생존 확인: pid일 수 없는 값(0, -1 등)은 살아 있지 않습니다. 이전에는 살아 있다고 읽거나 오류로 끝났습니다.
- 확장의 중단: 대기 중에 `uv` wrapper가 이미 끝났는데 자손이 파이프를 쥐고 있으면 10초 뒤 그 그룹을 강제로 끝냅니다
  (이전에는 보내지 않았습니다). 강제 종료 뒤의 대기에도 상한이 있습니다. 멈추는 중에 출력을 쏟는 kit을 SIGKILL하지
  않습니다.

배포본을 더하면서(ADR-0004, `d9e7cc5` 대비) POSIX에서 다음이 바뀌었습니다.

- 새 프로젝트 템플릿이 `kit/template/`에서 `kit/labkit/template/`로 옮겨 labkit의 package data가 되었습니다. 그래서
  run의 `env.kit_sha256`이 템플릿 파일(`CONTEXT.md`, `pyproject.toml`)도 덮고, 같은 코드에서 값이 한 번 바뀌며 리포트가
  한 번 다시 만들어집니다. 무엇도 이 값을 비교하지 않습니다.
- `init`은 무엇이든 만들기 전에 템플릿을 읽습니다. 읽을 수 없으면 `template_missing`으로 답하고 아무것도 만들지
  않습니다(이전에는 traceback으로 끝나며 반쯤 만든 프로젝트를 남겼고, 다시 `init`하면 비어 있지 않다고 거절했습니다).
- `/lab open`은 opener를 aelix의 터미널과 떼어 놓은 채(자기 session, 표준 스트림 없음) 시작합니다. macOS는 `open`,
  그 밖의 POSIX는 `xdg-open`이고, 그것이 없으면 이전처럼 `webbrowser`로 엽니다. opener를 시작하지 못하면 그렇다고
  답합니다. `webbrowser`가 열지 못한 것(이전과 같습니다)과 떼어 놓은 opener가 실패한 것(이전에는 그 오류가 aelix의
  터미널에 찍혔습니다)은 알리지 않고 열었다고 답합니다.
- 한 aelix에서 이 확장의 두 번째 사본은 설정되지 않고 stderr에 알립니다(A).
- 확장이 찾은 uv를 링크를 따라가지 않은 절대 경로로 씁니다(이전에는 링크를 풀었습니다). 그래서 mise의 shim으로 설치한
  uv에서 kit 명령이 mise 자체로 실행되어 실패하던 것이 고쳐지고, `/lab where`는 링크의 경로를 보여 줍니다.
- `/lab note`·`lab_note` 뒤의 리포트 예약과 `/lab where`의 uv 조회는 어떤 예외로도 명령을 실패시키지 않습니다. 이전에는
  기록한 뒤에 오류로 답할 수 있었습니다.

## G. 약속하지 않는 것

- 격리·샌드박스·승인. `lab_run` 스크립트는 사용자가 읽을 수 있는 모든 파일에 닿습니다(`lab_query`만 `data/`로 묶여 있습니다).
- 계보의 완전성. 입력 감지는 최선 노력입니다(pyarrow 직접 읽기처럼 훅이 없는 경로는 놓칩니다). 일부만 감지된
  경우 경고도 없습니다 — 경고는 입력이 하나도 없을 때만 나옵니다. 행 단위 계보와 선택(마킹) 기반 후속 분석은 없습니다.
- 무결성. 해시 체인·서명이 없고 불변성은 규약입니다.
- 원자성·복구·멱등성. 한 프로젝트의 run은 직렬화되지만, 강제 종료된 run은 `incomplete`로 남고 되돌리지 않습니다.
  프로젝트 밖에서 같은 파일을 건드리는 다른 프로세스는 막지 않습니다.
- 결정성과 다른 환경 재현. 재실행은 같은 환경을 가정하고 환경 지문을 비교하지 않습니다.
- 반출 정책·가림. 리포트에는 데이터 미리보기와 로그가 그대로 들어갑니다.
- 규모 한도. 명시적 상한이 없고 큰 데이터에서는 느려집니다.
- 스크립트가 가로챈 중단과 한 번만 중단하는 규칙의 비용(모든 플랫폼). 취소와 제한 시간은 kit의 처리기를 거쳐서만
  스크립트에 닿습니다(D1).
  - SIGINT를 무시하거나 예외를 올리지 않는 자기 처리기를 설치한 스크립트는 취소로 멈추지 않습니다. 확장이 기다리는
    10초 안에 끝나면 자기 결과(`ok`·`error`)가 남고, 넘기면 강제로 끝나 `incomplete`로 남습니다. POSIX에서 SIGINT를 기본
    동작으로 되돌린 스크립트는 요청 자체로 끝나 `running`으로 남고 `incomplete`로 보입니다(이전 구현과 같습니다).
    Windows에서는 SIGINT를 무시하거나 기본 동작으로 둔 스크립트에 묻지 않습니다 — 기본 동작이면 묻는 것이 프로세스를 끝냅니다.
  - 제한 시간의 시그널(POSIX SIGALRM, Windows SIGINT)을 가로챈 스크립트는 멈추지 않을 수 있습니다. 기록은 `timeout`입니다.
  - 구간마다 한 번만 중단하므로 `BaseException`을 잡는 스크립트나, 중단을 보통 오류로 바꿔 잡는 라이브러리를 부른
    스크립트는 다시 중단되지 않습니다. 시그널이 finalizer 안에서 처리되어도 그 한 번이 거기서 쓰입니다: Python의
    `__del__`이나 weakref 콜백 안이면 CPython이 예외를 보고하고 버리고, 닫지 않은 gzip writer나 쓰는 중인 zip 멤버처럼 I/O 객체 자신의
    finalizer 안이면 개발 모드가 아닌 한 보고 없이 버립니다. 이미 중단을 삼킨 스크립트는 터미널에서
    SIGKILL(`kill -9`)이나 Ctrl+\(SIGQUIT)로 끝낼 수 있으며, 기록은 `running`으로 남아 `incomplete`로 보입니다.
  - 시그널을 확인하지 않고 도는 C 확장 호출은 반환할 때까지 멈추지 않습니다.
  - 남은 창: 스크립트가 남긴, 예외를 올리는 처리기가 kit 처리기의 첫 명령에서 먼저 돌면 kit 처리기는 한 문장도 실행하지
    못합니다. 취소는 그 예외의 traceback에서 찾아 셉니다. 구간이 올린 예외와 구간을 떠나는 중에 처리기들이 올린 예외
    모두, 그리고 거기서 `__context__`·`__cause__`·예외 그룹의 구성원(뒤의 구성원부터)으로 이어지는 예외를 봅니다. 구간이
    올린 예외에서 64개까지, 처리기들이 올린 예외 하나하나에서 또 64개까지 봅니다. 그래서 스크립트가 그 예외를 잡고
    계속 돌거나, `except` 밖에서 새 예외를 올리거나, 그 가운데 하나에서 그보다 많은 예외 뒤에 두면(긴 연결, 큰 예외
    그룹의 앞쪽 구성원) 빠집니다. Windows에서는 그 구간의 타이머가 제한 시간의 SIGINT를 낸
    뒤에 이렇게 끊긴 취소를 세지 않습니다. 그 run은 어차피 `timeout`입니다.
    kit의 타이머가 낸 제한 시간은 구간이 끝날 때 시각으로 셉니다. kit의 타이머가 아닌 SIGALRM이 그 지점에서 끊기면
    세지 않습니다. 그런 처리기로 16개가 넘는 시그널이 연달아 오면 kit의 처리기를 되돌리지 못하고 run 기록을 잃을 수 있습니다.
- 한 프로세스에서 run을 여러 번 하는 호스트(시험, SDK 사용)에서 스크립트가 남긴 스레드의 출력. 마지막 run의 스트림만
  붙잡아 두므로, 더 오래된 run의 스트림으로 쓴 글은 마지막으로 비운 뒤에 그 스트림이 수거되면 잃을 수 있습니다.
  CLI는 run이 하나이므로 해당하지 않습니다. 같은 프로세스에서 CLI를 여러 번 부르는 호스트에서는, 앞선 run이 남긴
  스레드가 그 run의 stdout 버퍼로 쓰므로 뒤의 run의 결과 줄 가운데에 끼어들 수 있습니다. 그런 호스트에서 Windows의
  제한 시간이 있는 구간이 타이머 스레드를 시작하는 바로 그 순간에 취소가 처리되면(CPython 3.14, 또는 profile·trace
  함수가 설치된 경우) CPython의 스레드 시작 잠금이 잡힌 채 남을 수 있고, 그 뒤로 그 프로세스에서 시작한 스레드는
  돌지 않습니다. 뒤의 run의 제한 시간도 그렇고, 그 뒤에 데몬이 아닌 스레드를 시작한 호스트는 끝나지 못합니다
  (Windows의 타이머를 macOS·Linux에서 흉내 내어 재현했고, Windows 자체에서는 실행하지 않았습니다).
- 스크립트가 직접 만들어 비우지 않은 출력 스트림(자기 stdout 버퍼를 감싼 래퍼 등)의 글은 그 스트림이 비워지거나
  수거될 때 쓰이며, 그때가 run 뒤이면 run의 로그가 아니라 CLI의 stdout으로 갑니다(이전 구현과 같습니다).
- UTF-8로 쓸 수 없는 이름. POSIX는 UTF-8이 아닌 바이트를 짝 없는 surrogate로 넘깁니다. run이 읽거나 쓰는 그런 이름의
  파일(예: Windows에서 만든 압축 파일을 푼 Linux의 데이터 파일), run 동안 다른 프로세스가 프로젝트 안에 만들거나 지운
  그런 이름의 파일, 그런 경로에 있는 인터프리터는 run의 마지막 기록을 쓰지 못하게 해 run이 `running`으로 남아
  `incomplete`로 보이고, 그런 `LAB_SESSION`은 CONTEXT.md가 바뀐 뒤의 맥락 동기화에서 JSON 줄 없이 traceback으로
  끝납니다(이전 구현과 같습니다). 정적 입력 스캔은 짐작일 뿐이므로 그런 이름을 입력으로 넣지 않습니다(F1). 그런 이름을
  기록에 어떻게 담을지(이스케이프, 치환, 거절)는 정하지 않았습니다. Windows(NTFS)도 짝 없는 surrogate가 든 이름을 짝 없는
  UTF-16 단위로 담고 같은 str로 돌려주므로 같은 경우가 생깁니다: 정적 입력 스캔이 그런 이름을 빼고 run을 `ok`로 기록하는
  것을 Windows에서도 확인했습니다(측정). run이 그런 이름의 파일을 읽거나 쓰는 경우는 Windows에서 실행하지 않았습니다.
- 프로젝트 안의 디렉터리 링크([측정 기록](verification/2026-10-02-issue-8-windows-inventory.md), 후속 [#36](https://github.com/handochan/aelix-analytics/issues/36)).
  - (측정) `data/`가 심볼릭 링크이면 그 아래에 저장한 run의 출력이 기록에서 빠집니다(Linux·Windows CI, macOS 개발 기기):
    run 기록은 `outputs: []`이고, 입력은 링크를 푼 프로젝트 밖의 절대 경로(`external`)로 기록되며, 그 데이터셋에는 producer가
    없습니다. Windows의 junction이면 출력은 `data/derived/…`로 기록되지만 입력과 데이터셋 목록은 푼 절대 경로라, 데이터셋의
    producer가 이어지지 않습니다(`used_by`는 기록됩니다). 이전 구현과 같습니다(디렉터리 목록이 심볼릭 링크 아래로 내려가지 않고 junction 아래로는
    내려갑니다).
  - (측정) 프로젝트 안에 `runs/`를 가리키는 junction이 있으면 run이 그 아래의 자기 파일(`<링크>/<id>/stdout.txt` 등)을 출력으로
    기록합니다. 심볼릭 링크는 따라가지 않아 기록하지 않습니다.
  - 재실행은 두 종류 모두 링크를 푼 실제 위치로 판정합니다(F, 측정). 조상 디렉터리를 가리키는 junction은 실행하지 않았습니다.
  - 기록 교체는 링크를 따라가지 않습니다. 심볼릭 링크인 기록 파일(dotfiles로 관리하는 신뢰 목록 등)을 고쳐 쓰면 링크가
    보통 파일로 바뀌고 링크 대상은 이전 내용으로 남습니다(macOS에서 관찰, 이전 구현과 같습니다. Linux·Windows에서는 실행하지
    않았습니다).
- 대소문자와 유니코드 정규화(후속 [#36](https://github.com/handochan/aelix-analytics/issues/36)). macOS(APFS)는 입력한 대소문자를 기록에 그대로 둡니다: 다른 대소문자로 적은 스크립트 경로는 다른
  차트 정체성이 되고, 디렉터리를 다른 대소문자로 적은 절대 경로는 프로젝트 밖이라고 거절되며, 다른 대소문자로 읽은 입력은
  데이터셋의 producer와 이어지지 않고, 상위 디렉터리를 다른 대소문자로 적어 만든 프로젝트는 그 디렉터리에서 신뢰 목록과
  맞지 않습니다(macOS 조사에서 관찰, 이전 구현과 같습니다). NFC로 적은 이름으로 NFD로 저장된 파일을 여는 것도 APFS에서는
  같은 파일이라 같은 종류로 봅니다(열기는 관찰, 기록에 미치는 결과는 추론). Windows(NTFS)는 다른 대소문자의 경로를 실제 철자로
  풀고, NFD로 만든 이름을 그대로 두며 NFC 철자로는 찾지 않습니다(조사 실행 36899933022에서 측정). 대소문자를 구별하도록 표시한 Windows 디렉터리
  (`fsutil file setCaseSensitiveInfo`) 안의 프로젝트는 Linux처럼 읽어 `RUNS/x.py`를 모든 플랫폼의 규칙으로 거절합니다(측정).
  대소문자만 다른 데이터셋 파일이 함께 있을 수 있는 파일 시스템(Linux, 그렇게 표시한 Windows 디렉터리)에서 사람이 `data/`에
  직접 둔 `Sales.parquet`은 정렬 순서 때문에 view 이름 `sales`를 먼저 받고, kit이 가져온 `sales`는 `sales_2`가 됩니다(순수 함수로
  확인). kit이 정하는 이름은 소문자라 kit끼리는 겹치지 않습니다(`--name Sales`는 거절).
- 줄 끝(결정할 일: [#37](https://github.com/handochan/aelix-analytics/issues/37)). 저장소에 `.gitattributes`가 없고 Windows 러너의 Git은 `core.autocrlf=true`(시스템 gitconfig)라 그 체크아웃 — kit,
  템플릿, 시험 fixture — 은 CRLF입니다(`core.autocrlf=true`는 측정 실행의 러너 설정에서, 파일이 CRLF인 것은 조사 실행
  36899933022에서 측정). 그래서 Windows에서 빌드한 wheel과 그 `env.kit_sha256`은 다른 플랫폼의 것과
  다르고, Windows에서 텍스트 모드로 쓴 스크립트·템플릿 사본·기록도 CRLF라 같은 내용의 해시가 플랫폼마다 다릅니다(추론).
  git의 줄 끝 변환을 거쳐 플랫폼을 오간 프로젝트의 텍스트 입력과 코드는 `changed`로 보일 수 있습니다(추론).
- aelix 한 세션의 프로젝트 전환. 한 확장이 두 프로젝트를 번갈아 다룰 때 각 도구 호출의 journal·telemetry·맥락 블록은 그때
  활성인 프로젝트의 것이고 다른 프로젝트의 파일은 바뀌지 않았습니다(시험, aelix 0.1.0b2). 도구 호출이 도는 중에 `/lab use`로
  바꾸면 그 호출의 telemetry는 끝날 때 활성인 프로젝트에 기록되고 시작한 프로젝트에는 남지 않습니다(시험이 고정합니다.
  이전 구현과 같습니다. [#32](https://github.com/handochan/aelix-analytics/issues/32)).
- Windows의 한계. [측정 기록](verification/2026-09-24-issue-8-platforms.md)과 [Windows 인벤토리 기록](verification/2026-10-02-issue-8-windows-inventory.md)에서 실행한 것과
  추론한 것을 구별해 적습니다. 측정한 Windows 구성은 F에 있는 하나입니다. 이번에 측정한 긴 경로·콘솔·환경의 한계는
  후속 [#38](https://github.com/handochan/aelix-analytics/issues/38)이 다룹니다.
  - (측정) `taskkill /T`는 부모가 이미 끝난 자손에 닿지 않습니다. 그런 자손이 파이프를 쥐고 있으면 확장은 정해진 시간만
    기다리고 돌아오며 자손은 남습니다. 확장은 이를 정리 성공으로 적지 않고 stderr에 남깁니다.
  - (측정) GIL을 쥔 채 도는 C 코드 안의 주 스레드에는 그 호출이 반환할 때까지 취소가 닿지 않습니다. 요청 파일을 보는
    것이 Python 스레드이기 때문입니다. 파국적 정규식을 도는 run은 확장이 기다리는 10초 안에 멈추지 않아 강제로 끝났고
    `incomplete`로 남았습니다. POSIX에서는 C 수준 시그널 처리기가 GIL 없이 돌고 `_sre`가 시그널을 확인하므로 닿습니다.
    이 한계는 확장의 취소와 Windows의 제한 시간의 것입니다: 콘솔의 Ctrl+C는 같은 이유로 닿아, 정규식을 도는 run이
    `cancelled`로 기록됐습니다(측정, 의사 콘솔).
  - (측정) SIGINT 이벤트를 보지 않는 대기(자식 프로세스를 기다리는 `subprocess` 호출 등)는 취소가 와도 그 대기가 끝날
    때까지 이어집니다. 120초 자는 자식을 기다리는 run이 10초 뒤 강제로 끝나 `incomplete`로 남았습니다. 콘솔의 Ctrl+C에서는
    같은 콘솔의 자식이 그 Ctrl+C로 끝나 대기가 풀렸고 run은 `cancelled`였습니다(측정, 의사 콘솔). 대기 자체가 그 이벤트를
    보는 것은 아니므로, 자기 프로세스 그룹이나 콘솔 없이 시작한 자식, Ctrl+C를 무시하는 자식, 그 밖의 대기는 콘솔의 Ctrl+C에도
    그 대기가 끝날 때까지 이어진다고 봅니다(실행하지 않았습니다).
  - (측정, 한 번) 스크립트가 자식을 시작하는 바로 그때(`CreateProcess` 안) 콘솔의 Ctrl+C가 오면 run은 `cancelled`로
    기록되지만, 그 Ctrl+C 뒤에 콘솔에 붙은 자식은 그것을 받지 못해 run이 끝난 뒤에도 남았습니다(의사 콘솔). POSIX에서는
    실행하지 않았습니다.
  - (추론) Windows의 제한 시간도 Python 스레드가 SIGINT로 올리므로, 위 두 경우에 같은 이유로 닿지 않는다고 봅니다.
    실행하지 않았습니다.
  - 강제 종료는 `cancelled`가 아니라 `incomplete`입니다(D1).
  - (측정) 콘솔의 Ctrl+Break(SIGBREAK)는 uv를 곧바로 `0xC000013A`로 끝내므로 설치본 `analytics`가 kit의 기록보다 먼저
    돌아옵니다: 셸에 돌아온 순간 그 run은 `running`이고, kit은 나중에 `cancelled`를 기록하며(자는 kit은 그 잠이 끝난 뒤),
    kit의 결과 줄은 프롬프트 뒤에 나옵니다(의사 콘솔에서 읽음). 프롬프트 뒤에 시작한 run은 첫 run이 끝난 뒤에야
    시작했습니다(첫 run의 끝보다 0.7초 뒤): kit이 끝날 때까지 프로젝트의 run 잠금을 쥐고 있기 때문이라고 봅니다. kit이 끝난
    뒤 남은 프로세스는 없었습니다(의사 콘솔에서 3.12·3.11 설치본, 고전 콘솔에서 3.12 설치본). 콘솔의 취소는 Ctrl+C입니다.
  - (측정) run이 도는 중에 의사 콘솔을 닫으면(`ClosePseudoConsole`, 콘솔 닫기 이벤트) 기록은 `incomplete`로 남았습니다.
    남은 프로세스는 없었습니다.
  - (추론) 콘솔의 시간 창. 두 번째 Ctrl+C가 CRT·CPython이 처리기를 다시 설치하는 짧은 창에 오면 kit(기록은 `running`으로
    남아 `incomplete`)이나 런처가 끝날 수 있고, uv가 자기 처리기를 설치하기 전에 온 Ctrl+C는 Ctrl+Break처럼 uv를 끝내며,
    kit이 import하는 동안 온 Ctrl+C는 run 없이 traceback으로 끝납니다(POSIX에서 130을 관찰, Windows는 `0xC000013A`로 예상).
    실행하지 않았습니다. POSIX 쪽의 같은 창과 늦은 두 번째 Ctrl+C는 [#32](https://github.com/handochan/aelix-analytics/issues/32)에 있습니다.
  - (측정) PowerShell이 `analytics status`의 출력을 변수에 담으면(`$o = & analytics.exe status`; 파이프로 넘겨도 같다고
    봅니다) 그 UTF-8 바이트를 콘솔 코드
    페이지로 읽어 한국어가 깨집니다: 코드 페이지 437과 949에서 그랬습니다. 콘솔에 바로 쓰일 때는 바릅니다(F). 받기 전에
    `[Console]::OutputEncoding = [Text.Encoding]::UTF8`로 두면 바르게 읽는다고 봅니다(추론, 실행하지 않았습니다).
  - (측정) 긴 경로, LongPathsEnabled=1. 프로젝트 루트가 그 환경의 `python.exe`를 MAX_PATH(260) 안에 둘 만큼 짧으면(루트
    230자, 계산한 인터프리터 경로 255자; 이 시험의 kit은 체크아웃의 인터프리터로 돌았습니다) run이 260자를 넘는 파일(가장
    깊은 파일 365자)을 써도 기록·리포트가 됩니다. 루트가 MAX_PATH를
    넘으면(300자) 그 루트를 작업 디렉터리로 하는 프로세스는 시작하지 않습니다(CreateProcess, WinError 267): 확장의 kit 명령과
    환경 동기화는 `spawn_failed`로 답하고, `/lab init`은 `lab.json`을 만들고 프로젝트를 활성화한 뒤 `spawn_failed`로 답하며,
    `/lab note` 뒤의 리포트 빌드는 알리지 않고 시작하지 않습니다. `/lab note`와 맥락 블록은 aelix 프로세스 안에서 돌아
    됩니다. 런처는 uv를 작업 디렉터리 없이 시작하므로, 더 짧은 디렉터리에서 `LAB_PROJECT`로 그 루트를 가리키면 `init`(실제
    uv 동기화, 환경의 인터프리터 경로 325자)과 `status`가 됐습니다(설치본 `analytics.exe`). 그 루트를 작업 디렉터리로 한
    프로그램은 같은 오류로 시작하지 않았습니다(측정한 것은 Python 하나).
  - (측정) 긴 경로, LongPathsEnabled=0(레지스트리를 바꾼 뒤 새 프로세스에서 0으로 읽힘). Python이 260자를 넘는 경로에
    파일을 만들지 못합니다: 루트 230자에는 프로젝트를 만들 수 없었고(앞선 실행 36986490189; 맥락 사본 `.lab/context/…`이 230+52자로
    260을 넘는다는 것은 계산), 300자
    루트에 kit의 `init`은 JSON 줄 없이 traceback으로, `/lab init`은 `internal error`로 끝났습니다. 루트 203자에서는 프로젝트
    만들기·데이터 가져오기·리포트가 됐고, 260자를 넘는 출력을 쓰는 run은 matplotlib이 차트 파일을 쓰지 못해 `error`로
    기록됐습니다. 그 앞에서 `lab.save`가 쓴 285자 경로의 데이터셋은 디스크에 있었지만 run의 출력 목록은 비어 있었습니다
    — 출력 기록을 잃습니다.
  - Windows에서 확장이 자식에게 넘기는 환경은 플랫폼의 기본 변수와 허용 목록뿐이라 `USERNAME`·`HOMEDRIVE`·`HOMEPATH`와
    사용자가 둔 `PYTHONUTF8` 등이 빠집니다. 그래서 `getpass.getuser()`를 부르는 스크립트는 `lab_run`에서 실패하고
    `analytics run`에서는 될 수 있습니다(추론, 실행하지 않았습니다).
  - (추론) 교체 중인 기록을 그 순간 여는 reader는 거절될 수 있고, 그러면 그 기록을 없는 것으로 읽습니다.
  - (추론) SIGINT가 무시·기본 동작인지 확인하는 것과 올리는 것은 두 호출이라, 그 사이에 스크립트가 기본 동작으로 바꾸면
    프로세스가 끝납니다.
  - aelix가 끝날 때 아직 멈추는 중인 명령은 그 뒤 강제로 끝내지 않습니다. 요청 디렉터리는 kit이 볼 수 있도록 임시
    디렉터리에 남습니다(POSIX에서는 SIGINT를 이미 보낸 상태입니다).
- 설치본과 사본(ADR-0004).
  - 사본 하나만 설정하는 확인(A)은 두 사본이 모두 이 버전 이상일 때만 됩니다. 이 확인이 없는 이전 버전의 체크아웃을
    aelix의 extensions 디렉터리에 symlink한 채 설치본을 더하면 두 사본이 모두 설정되어, 도구 호출마다 telemetry가 두 번
    기록되고 맥락 블록이 두 번 붙습니다. 설치본을 쓰면 symlink를 지웁니다(README).
  - aelix를 다시 설치하면 그 환경에 더한 이 wheel이 빠집니다: `uv tool install --force aelix`는 측정했고, 같은 명령을
    `--with` 없이 실행하는 `install.sh`는 추론입니다. 다시 설치해야 합니다.
  - wheel을 zip import로 쓰는 설치는 지원하지 않습니다. `kit_dir()`이 거절합니다: zip 안의 kit도 자식에서 실행되지만
    `env.kit_sha256`에 빈 입력의 해시를 기록하고, aelix 프로세스는 `core`를 파일로 로드합니다. 실행 중인 run이
    import하는 kit을 그 자리에서 업그레이드하는 경우는 시험하지 않았습니다.
  - `analytics`라는 이름의 다른 명령이 PATH에 있으면 PATH 순서가 어느 것이 도는지 정합니다. 체크아웃의 `bin/lab`과
    설치본의 `analytics`가 한 프로젝트를 번갈아 다루면 run마다 `env.kit_sha256`이 다를 수 있습니다.
  - kit이 보여 주는 글과 만드는 파일은 명령을 가리킬 때 아직 `lab`이라고 씁니다. 설치본의 사용자가 만나는 것은 프로젝트가
    없을 때의 hint(`create one with: lab init DIR`), 리포트의 run 쪽 재실행 안내(`lab run --rerun-of …`), 템플릿
    `.gitignore`의 주석(`lab report`)입니다. kit은 어느 진입점이 불렀는지 모르므로 체크아웃의 `bin/lab`에는 맞고, 설치본에서는
    `lab`을 `analytics`로 읽어야 합니다. 명칭 전환(#9)이 함께 바꿉니다.
  - (추론) CPython 3.11 호스트의 `asyncio.wait_for`는 기다리던 자식이 끝나는 바로 그 순간 온 취소를 삼킵니다. 그 도구
    호출은 취소 대신 결과를 돌려주며, 자식의 기록은 이미 끝나 있습니다. aelix가 그 결과를 어떻게 다루는지는 확인하지
    않았습니다.
  - 설치본을 업그레이드한 뒤 aelix를 다시 시작하지 않으면 aelix 프로세스는 이전 확장·`core`를, kit의 자식은 새 kit을
    씁니다. `/reload`로는 바뀌지 않습니다. kit의 버전은 run 기록(`env.kit_version`·`kit_sha256`)과 `lab.json`(만들 때의
    버전)에만 적힙니다. journal 이벤트와 데이터셋 메타데이터에는 버전이 없으므로, 그동안 aelix 프로세스의 이전 `core`가
    쓴 journal 이벤트(`/lab note`·`lab_note`, 맥락 동기화)는 기록으로 구별할 수 없습니다. 업그레이드한 뒤에는 aelix를 다시
    시작합니다.
  - 런처의 신호 처리기는 `main()`이 시작할 때 설치됩니다. 인터프리터가 시작하는 동안 온 Ctrl+C는 traceback이나
    SIGINT로 런처를 끝낼 수 있습니다(시작한 자식은 아직 없습니다). 실행하지 않았습니다. Windows에서 자식을 시작하는 순간
    온 Ctrl+C를 다시 보내지 않는 것은 F에 있습니다.
  - 에이전트가 aelix의 bash 도구로 설치본의 `analytics`를 부르면 그 기록은 작성자 `human`으로 남고, 확장의 telemetry도 이를
    따로 표시하지 않습니다(체크아웃의 `bin/lab`도 이미 그렇습니다).
- 실행하지 않은 것(`not_run`). 코드가 있다는 사실을 동작 증거로 쓰지 않고, Aelix 호스트가 Windows를 지원하더라도 이 확장의
  Windows 지원으로 옮겨 적지 않습니다. 이유와 함께 적습니다([측정 기록](verification/2026-10-02-issue-8-windows-inventory.md)).
  - 사람이 보이는 콘솔 창에서 누른 키, Windows Terminal·OpenConsole을 호스트로 한 콘솔: 호스트 러너에는 창이 있는 대화형
    세션이 없어, 키 대신 의사 콘솔의 입력과 도우미 프로세스의 콘솔 이벤트로 측정했습니다(위).
  - POSIX의 실제 터미널에서 설치본 런처·실제 uv·kit으로 보내는 Ctrl+C의 회귀 시험: 조사에서 macOS의 pty로 한 번 실행했지만
    (`18f7293`의 설치본, `cancelled`·종료 코드 3, 로컬 조사 증거) 측정 커밋에서는 다시 돌리지 않았고 Linux에서는 실행하지
    않았습니다. 그 pty 하네스는 #32로 넘겼습니다(저장소 시험은 런처의 동작을 stand-in uv로 시험합니다).
  - 시스템 로캘 ko-KR(ANSI·OEM 코드 페이지 949), 한국어 표시 언어, 한국어 사용자 프로필 경로: 러너는 ANSI 1252·OEM 437이고
    시스템 로캘은 재부팅해야 바뀝니다. 콘솔 코드 페이지 949는 측정했습니다(F).
  - Windows의 실제 aelix 세션, 어느 플랫폼에서든 모델과의 실제 aelix 세션: aelix 통합 시험은 aelix의 로더와 hook·도구를 직접
    부르고 모델을 쓰지 않습니다.
  - 다른 사용자의 프로세스에 대한 `WindowsProcessControl.alive`: 러너에 다른 사용자의 세션이 없습니다.
  - 실제 opener(`os.startfile`의 실제 연결, `open`, `xdg-open`): 열린 창을 CI에서 확인할 수 없어 opener를 바꿔 넣어
    시험합니다. 설치본으로 실제 uv를 쓴 `analytics demo`와 README의 `--with-executables-from` 설치는 시험에 넣지 않았습니다.
  - 측정한 구성 밖의 Windows: 클라이언트 판(같은 build의 Windows 11 24H2 포함)과 Windows 10, Server 2022, 표준 사용자 토큰,
    Developer Mode를 끈 기본 설정, x64 밖의 아키텍처. 그 밖의 플랫폼에서도 측정한 조합(F) 밖의 아키텍처, CPython 3.11·3.12 밖의
    설치 환경과 측정하지 않은 패치 릴리스.
  - LongPathsEnabled=0에서 위의 긴 경로 경로 밖의 동작: 0은 러너의 설정이 아니어서, 1에서 측정한 경로들만 마지막 단계에서
    새 프로세스로 한 번 더 돌렸습니다.
  - Windows에서 정규식·자식 대기 중에 오는 제한 시간(위의 추론): 그 순간에 제한 시간을 맞추는 시험을 두지 않았고, 같은
    Python 스레드 경로인 확장의 취소에서 추론했습니다.
  - aelix나 run이 도는 동안의 업그레이드: 실행 중인 프로세스 아래에서 설치본을 바꾸는 하네스가 없습니다. 업그레이드한 뒤에는
    aelix를 다시 시작합니다(위).
  - 콘솔의 시간 창(위의 추론): 측정은 경우마다 한 번이고, 그 창을 겨냥해 맞출 방법이 없습니다.
  - 네트워크·클라우드 볼륨(SMB, OneDrive), FAT·exFAT·ReFS, 백신의 간섭: 러너의 디스크는 로컬 NTFS입니다.
  - 콘솔 파일 기술자에 바이트를 직접 쓰는 출력, 고전 콘솔의 글 순서·인코딩: 하네스는 고전 콘솔의 화면을 읽지 않고, 측정한
    출력은 Python의 콘솔 경로를 지난 것입니다(F).
  - 위에서 (추론)으로 적은 Windows 항목: 교체 중인 기록의 reader, SIGINT 확인과 올림 사이의 경쟁, 한 프로세스에서 run을 여러
    번 하는 호스트의 스레드 시작 잠금(macOS·Linux에서 흉내 내어 재현): 모두 짧은 경쟁 창이라 그 순간을 겨냥해 맞출 방법이
    없습니다.
  - 위의 항목에서 "실행하지 않았습니다"로 적은 나머지: Windows에서 run이 UTF-8이 아닌 이름의 파일을 읽거나 쓰는 것, Linux·
    Windows에서 심볼릭 링크인 기록 파일을 고쳐 쓰는 것, 자기 프로세스 그룹이거나 콘솔 없이 시작했거나 Ctrl+C를 무시하는 자식을
    기다리는 run에 오는 콘솔의 Ctrl+C, POSIX에서 자식을 시작하는 순간의 Ctrl+C, `[Console]::OutputEncoding`을 UTF-8로 둔
    PowerShell의 캡처, 확장의 자식 환경에서 빠지는 변수(`getpass`), 런처의 콘솔 출력: 이 PR이 그 경우의 시험을 두지 않았고,
    후속(#36·#38·#32)이 다룹니다.
  - aelix의 `install.sh`: 이 저장소의 시험은 aelix를 `uv tool install`과 `uv run --with`로만 설치합니다(`install.sh`가 이
    wheel을 빼는 것은 위의 추론).
  - Windows에서 다른 프로세스가 보내는 SIGTERM: Windows에는 처리기에 SIGTERM을 보낼 방법이 없습니다.
  - 프로젝트 안에서 조상 디렉터리를 가리키는 junction, Developer Mode만 켜거나 관리자 권한만으로 만든 링크, `mklink`·`New-Item`으로
    만든 링크: 시험은 관리자이며 Developer Mode가 켜진 러너에서 `os.symlink`와 `_winapi.CreateJunction`으로 링크를 만들었습니다.
