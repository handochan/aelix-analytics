# Aelix Analytics 계약 v1

Aelix Analytics가 현재 **약속하는 것과 약속하지 않는 것**의 전부입니다. 코드가 기준이며,
이 문서와 코드가 다르면 코드가 맞고 문서를 고칩니다. 스키마 이름에 버전(`/1`)이 붙습니다.

## A. aelix 확장 표면 — 쓰는 것 전부

| 표면 | 쓰임 |
|---|---|
| `setup(aelix)` | 단일 파일 `extension/aelix_lab.py`의 팩토리 |
| `register_flag("lab-project", type="str")` | 프로젝트 지정 |
| `register_tool` × 6 | `lab_status`, `lab_query`, `lab_import`, `lab_run`, `lab_note`, `lab_init` — 모두 `execution_mode="sequential"`, 스키마는 `pattern` 없는 평범한 JSON Schema, `prompt_snippet`·지침은 줄마다 200자 이하 |
| `register_command("lab")` | 사람 명령 |
| `on("before_agent_start")` | `BeforeAgentStartResult(system_prompt=기존 + 맥락 블록)` — 그 요청에만 적용, 누적되지 않음 |
| `on("tool_execution_start" / "tool_execution_end")` | 모든 도구 호출을 `.lab/telemetry.jsonl`에 기록 |
| `on("agent_end")` | 분리된 프로세스로 `report --if-changed` 시작 |

쓰지 않는 것: `aelix-plugin.toml` 매니페스트, entry point 패키징, provider·login·API adapter, 메시지 렌더러,
descriptor·TUI 위젯, MCP, subprocess hook, `send_message`·`append_entry` 같은 세션 조작.
확장은 aelix 프로세스 안에서 stdlib와 aelix API, 그리고 `kit/labkit/core.py`와 그 형제 `kit/labkit/_platform.py`(둘 다 stdlib 전용)만 import합니다.
두 파일은 kit 경로를 `__path__`로 갖는 합성 package의 submodule로 로드됩니다. package 이름에 kit 경로의 해시가 들어가고
로드가 끝나면 `sys.modules` 항목을 지우므로, 호스트 환경에 설치된 `labkit`을 가리거나 두 pack이 캐시를 공유하는 일이 없습니다.
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

`uv run --project P python -m labkit --project P <command> …` (`PYTHONPATH=<pack>/kit`).

- stdout 마지막 줄이 JSON 하나: `{"ok": true, …}` 또는 `{"ok": false, "error": {code, message, hint?, details?}}`.
  스크립트의 stdout·stderr는 run 동안 파일로 돌려지므로 섞이지 않고, 스크립트가 `sys.stdout`을 닫아도 이 줄은 남습니다.
  스크립트가 남긴 스레드가 run이 끝난 뒤에 쓰는 출력은, CLI가 결과를 쓰기 전에 쓴 것은 결과 줄 앞에, 그 뒤에 쓴 것은
  결과 줄 뒤에 오고 결과 줄 가운데에는 끼어들지 않습니다. run 동안 잡아 둔 `sys.stdout`(그것으로 만든 logging 처리기
  등)으로 써도, 파이프가 한 번에 받지 못할 만큼 결과 줄이 길어도 그렇습니다(이전 구현과 같습니다). Python 스트림을
  거치지 않고 fd 1에 바로 쓰는 출력(`os.write(1, …)`, C 코드)은 이전 구현처럼 이 보장 밖입니다. 스크립트를 실행한
  명령은 결과 줄 앞에 늘 빈 줄 하나를 쓰므로, 그 스레드의 출력이 줄바꿈으로 끝나지 않아도 결과와 한 줄에 붙지 않습니다.
  스크립트를 실행하지 않은 명령은 JSON 한 줄만 씁니다. 확장은 JSON 객체로 읽히는 마지막 줄을 결과로 받으므로, 결과
  뒤에 JSON 객체를 출력하는 스레드는 결과로 오인될 수 있습니다.
- 종료 코드: 0 성공, 1 처리된 오류, 2 사용법 오류, 3 run은 기록됐으나 `ok`가 아님, 130 중단.
- 명령: `init` `status` `context` `run` `import` `query` `note` `report [--if-changed]` `demo`.
- `run`이 코드 스냅샷이나 첫 기록(`running`)을 쓰지 못하면 만든 run 디렉터리를 지우고 `snapshot_failed`·`record_failed`로
  답합니다. hint는 원인에 따라 경로를 줄이라는 것(운영체제가 이름이나 경로가 너무 길다고 답했거나 경로의 한 구성
  요소가 255자를 넘음), 디스크 공간, 제목·스크립트 이름·`LAB_ACTOR`·`LAB_SESSION`을 UTF-8로 쓸 수 있는 값으로 하라는
  것, `runs/` 쓰기 권한 중 하나입니다. run 디렉터리를 만들기 전(잠금, `runs/` 자체)과 첫 기록 뒤(로그 열기)의 실패는
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
| 취소 | 확장이 `uv run` 자식에 SIGINT를 보내고 uv가 kit에 전달. SIGTERM도 취소 | 확장이 임시 디렉터리에 요청 파일을 만들고(`LAB_CANCEL_FILE`), kit의 감시 스레드가 주 스레드에 SIGINT를 올림(`signal.raise_signal`). SIGBREAK(콘솔의 Ctrl+Break)도 취소로 받지만 콘솔에서는 실행하지 않았음(G) |
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
이식 가능한 경계의 구현 방식은 [ADR-0003](decisions/0003-portable-boundaries-and-in-process-kit-loading.md)입니다.
아래 표는 **현재 코드가 실제로 하는 것**이며, 채택한 목표와 구별합니다. 남은 하한·패키징은 [#8](https://github.com/handochan/aelix-analytics/issues/8)에서 적용합니다.

| 항목 | 현재 코드 |
|---|---|
| 선언된 Python 하한 | `pyproject.toml`·`kit/template/pyproject.toml`·`uv.lock` 모두 `>=3.12`. ADR-0002가 채택한 진입점 `>=3.11`은 아직 적용되지 않았습니다 |
| 실행으로 확인한 범위 | ⟦이 코드를 Ubuntu 24.04와 Windows Server 2025(GitHub Actions), macOS 26.6.2 arm64(개발 기기)에서 실행했습니다([측정 기록](verification/2026-09-24-issue-8-platforms.md))⟧. Windows에서 확인한 것, 선언한 한계, 실행하지 않은 것은 G절에 있습니다. 실행하지 않은 조합으로 지원을 넓혀 적지 않습니다 |
| 검증된 조합 | ⟦macOS 26.6.2 arm64 / CPython 3.12.13(저장소 시험·스모크·aelix 0.1.0b2 통합)과 3.13·3.14(저장소 시험). Ubuntu 24.04·Windows Server 2025 x64 / CPython 3.12.x(저장소 시험·스모크·aelix 0.1.0b2 통합). 두 OS의 CPython 3.11은 aelix 프로세스가 로드하는 stdlib 부분만⟧ |
| 프로세스 경계 | stdlib·aelix API·`kit/labkit/core.py`와 `_platform`으로 제한되는 것은 **aelix 확장 프로세스**입니다. 확장은 `uv run --project` 자식 프로세스로 CLI를 띄웁니다(`extension/aelix_lab.py`의 `run_kit`). 그 CLI 프로세스는 `runner`·`data`·`report`를 자기 프로세스에서 import하고, `data.py`와 SDK가 그 안에서 duckdb·polars를 import합니다. 실행되는 스크립트도 같은 프로세스입니다 |
| 잠금·타임아웃·종료 | `kit/labkit/_platform.py`의 네 포트 뒤에 있습니다: `FileLocks`(journal·run·report 잠금), `AtomicFiles`(기록 교체), `ProcessControl`(자식 생성·생존 확인·중단 요청·강제 종료), `RunControl`(run 제한 시간과 취소, D1). 구현은 `core.PLATFORM`(모듈을 불러올 때 `_platform.detect()`가 한 번 고른 것)이 기본값이고 `Project(root)`가 그것을 씁니다. 시험과 호출자는 `Project(root, platform=…)`로 다른 구현을 넣습니다. 환경 변수나 기록 속 문자열로 구현을 고르지 않습니다. 호출부에는 `sys.platform` 분기가 없고, 예외는 `/lab open`의 브라우저 실행(`extension/aelix_lab.py`)입니다. POSIX 구현은 `fcntl.flock`, `os.replace`, SIGINT로 묻기, `setitimer`·SIGALRM, `start_new_session`과 `os.killpg(SIGKILL)`입니다. Windows 구현은 `msvcrt.locking`(경합이면 다시 시도), 다른 프로세스가 연 파일의 교체 거절(WinError 5·32·33)을 약 2초 동안 다시 시도하는 `os.replace`, 요청 파일과 `signal.raise_signal(SIGINT)`로 묻기, `threading.Timer`와 `raise_signal`로 제한 시간, `CREATE_NEW_PROCESS_GROUP`과 `taskkill /F /T`입니다. 콘솔 이벤트(`CTRL_BREAK_EVENT`)와 `_thread.interrupt_main`은 쓰지 않습니다 — 측정에서 앞의 것은 `uv`를 곧바로 끝내 그 자식을 기록 없이 남겼고, 뒤의 것은 잠든 주 스레드를 깨우지 못했습니다 |
| 생존 확인 | `running` 기록의 pid가 pid일 수 없는 값(0, 음수, bool, 플랫폼 범위 밖)이면 살아 있지 않습니다. 다른 사용자의 프로세스가 그 id를 가지면 재사용으로 보고 살아 있지 않다고 답합니다. 운영체제가 답하지 않으면 살아 있다고 보아 기록을 낮추지 않습니다. 같은 사용자의 프로세스가 재사용한 pid는 구별하지 못합니다 |
| 파일명 — 거절 | Windows 예약 장치명(`CON` `PRN` `AUX` `NUL` `CONIN$` `CONOUT$`, 숫자 1–9와 위 첨자 ¹²³이 붙은 `COM`·`LPT` — 첫 점 앞부분을 끝 공백을 빼고 대소문자 없이 비교)은 **사람이나 에이전트가 새 이름을 정하는 두 지점에서만** 거절합니다: `lab_import --name`과 `lab_run`이 만드는 `scripts/<name>.py`. 이미 있는 이름은 거절하지 않습니다 |
| 파일명 — 스크립트 경로 | run은 어느 플랫폼에서나 같은 뜻으로 읽히는 프로젝트 상대 경로만 기록합니다. 루트(`/`·`\`)나 드라이브(한 글자 뒤 콜론: `C:`, `é:`)로 시작하는 경로, 첫 구성 요소가 `~`로 시작하는 경로, `/`와 `\`로 나눈 구성 요소가 비었거나 `.`·`..`인 경로, 관리 디렉터리(`.venv` `.lab` `runs` `report`) 안의 경로 — `\` 구분자와 대소문자 변형 포함 — 는 기록하기 전에 `script_location`으로 거절합니다. 이전 기록을 재실행할 때도 같은 규칙을 적용하고, 링크를 따라간 실제 위치가 프로젝트 안이며 관리 디렉터리 밖인지 확인합니다 |
| 파일명 — run 안 | run 안의 `lab.save`와 스크립트가 직접 쓰는 경로는 거절하지 않습니다. 보존된 스크립트는 출력 파일이 지워진 뒤에도 다시 실행돼야 하기 때문입니다. 대신 run의 `warnings`에 남깁니다 — 출력과 차트 파일의 **모든 경로 구성 요소**를 검사하므로 `data/derived/con/value.parquet`도 걸립니다. 이 경고는 다른 경고를 가리지 않도록 묶어서 마지막에 둡니다 |
| 파일명 — 차트 | kit이 저장하는 차트 파일(`lab.chart`의 이미지·plotly JSON·플롯 데이터)만 이식 가능한 이름으로 바꿉니다(`con` → `con_.png`). 정체성 `script::id`는 생성된 id까지 그대로입니다. 이 대응은 단사입니다 — 차트 id는 slug이라 `_`를 포함할 수 없으므로 `con-file` 같은 보통 id와 충돌하지 않습니다. 스크립트가 차트 id 본래 이름에 쓴 파일(차트 `con`의 `charts/con.png`, 대소문자를 구별하지 않는 파일 시스템에서는 `charts/CON.png`도)은 이전 구현이 그 차트를 쓰거나 덮어쓴 파일이므로 따로 차트로 기록하지도 경고하지도 않고, 그 차트가 버려지면 함께 지웁니다. 그 밖에 스크립트가 run 디렉터리에 직접 쓴 파일(`register_loose_files`)은 스크립트가 정한 이름 그대로 차트가 되며 위 경고로만 알립니다 |
| 경로·인코딩 | 리포트의 절대 경로 링크는 기록한 플랫폼 기준으로 해석한 `file://` URL입니다(`C:\…` → `file:///C:/…`, `\\server\share\…` → `file://server/share/…`). 그래서 POSIX에서 이름이 `X:\`나 `\\`로 시작하는 프로젝트 파일은 그 Windows 경로로 링크되거나, 공유 이름이 없으면 링크되지 않습니다. CLI는 인자를 읽기 전에 표준 스트림을 UTF-8(`errors='replace'`)로 고정하고, 텍스트를 읽는 파일·자식 프로세스 호출은 인코딩을 명시합니다. run 로그도 UTF-8이며 인코딩할 수 없는 문자(짝 없는 surrogate)는 `?`로 씁니다. run 동안 stdout·stderr는 파일 기술자와 Python 스트림을 함께 바꾸고, 출력은 이전처럼 블록 단위로 버퍼링됩니다. 이렇게 해서 콘솔 전용 스트림이 파일에 쓰지 못하는 문제를 피하려는 것은 의도이며, 네이티브 대화형 콘솔에서는 실행하지 않았습니다(`not_run`) |
| 사람용 진입점 | `bin/lab`은 POSIX 셸 스크립트입니다. Windows 대응 진입점은 없습니다 |
| 배포 형태 | 루트 `pyproject.toml`이 `package = false`입니다. 설치 가능한 배포본이 없고 pack 경로에서 직접 실행합니다 |

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
  UTF-8이 아닌 바이트)도 run 디렉터리를 지우고 답합니다. 스크립트가 `sys.stdout`이나 `sys.__stdout__`을 닫아도 CLI의 JSON
  줄이 남습니다.
- 홈이 없는 `~user/…` 경로(`lab.path`, `lab.chart`의 파일, 선언한 입력, 정적 입력 스캔)는 이전처럼 `RuntimeError`로
  끝나지 않고 적힌 그대로 프로젝트 아래의 경로로 읽습니다. `~`로 시작하는 첫 구성 요소는 그것이 가리키는 홈 디렉터리가
  실제로 있을 때만 펼칩니다. Windows의 Python은 없는 사용자에게도 현재 사용자 프로필 옆의 홈을 추측하므로(`~$budget.xlsx`
  → `C:\Users\$budget.xlsx`) 이전에는 Windows에서 그런 경로를 `C:\Users` 아래의 없는 경로로 읽었습니다. 이 규칙으로
  모든 플랫폼이 같게 읽습니다. 새 run의 스크립트 경로로는 거절합니다(F 표).
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
  `ok`입니다. 이전 run들의 출력 스트림은 인터프리터가 끝날 때도 비웁니다. run 뒤의 CLI 결과는 새 줄에서 시작합니다(D).
- 생존 확인: pid일 수 없는 값(0, -1 등)은 살아 있지 않습니다. 이전에는 살아 있다고 읽거나 오류로 끝났습니다.
- 확장의 중단: 대기 중에 `uv` wrapper가 이미 끝났는데 자손이 파이프를 쥐고 있으면 10초 뒤 그 그룹을 강제로 끝냅니다
  (이전에는 보내지 않았습니다). 강제 종료 뒤의 대기에도 상한이 있습니다. 멈추는 중에 출력을 쏟는 kit을 SIGKILL하지
  않습니다.

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
    모두, 그리고 거기서 `__context__`·`__cause__`·예외 그룹의 구성원으로 이어지는 예외를 가장 나중 것부터 64개까지
    봅니다. 그래서 스크립트가 그 예외를 잡고 계속 돌거나, `except` 밖에서 새 예외를 올리거나, 그보다 많은 예외 뒤에
    두면(긴 연결, 또는 큰 예외 그룹의 앞쪽 구성원) 빠집니다. Windows에서는 그 구간의 타이머가 제한 시간의 SIGINT를 낸
    뒤에 이렇게 끊긴 취소를 세지 않습니다. 그 run은 어차피 `timeout`입니다.
    kit의 타이머가 낸 제한 시간은 구간이 끝날 때 시각으로 셉니다. kit의 타이머가 아닌 SIGALRM이 그 지점에서 끊기면
    세지 않습니다. 그런 처리기로 16개가 넘는 시그널이 연달아 오면 kit의 처리기를 되돌리지 못하고 run 기록을 잃을 수 있습니다.
- 한 프로세스에서 run을 여러 번 하는 호스트(시험, SDK 사용)에서 스크립트가 남긴 스레드의 출력. 마지막 run의 스트림만
  붙잡아 두므로, 더 오래된 run의 스트림으로 쓴 글은 마지막으로 비운 뒤에 그 스트림이 수거되면 잃을 수 있습니다.
  CLI는 run이 하나이므로 해당하지 않습니다.
- UTF-8로 쓸 수 없는 이름. POSIX는 UTF-8이 아닌 바이트를 짝 없는 surrogate로 넘깁니다. run이 읽거나 쓰는 그런 이름의
  파일(예: Windows에서 만든 압축 파일을 푼 Linux의 데이터 파일)은 run의 마지막 기록을 쓰지 못하게 해 run이 `running`으로
  남아 `incomplete`로 보이고, 그런 `LAB_SESSION`은 CONTEXT.md가 바뀐 뒤의 맥락 동기화에서 JSON 줄 없이 traceback으로
  끝납니다(이전 구현과 같습니다). 정적 입력 스캔은 짐작일 뿐이므로 그런 이름을 입력으로 넣지 않습니다(F1). 그런 이름을
  기록에 어떻게 담을지(이스케이프, 치환, 거절)는 정하지 않았습니다.
- Windows의 한계. [측정 기록](verification/2026-09-24-issue-8-platforms.md)에서 실행한 것과 추론한 것을 구별해 적습니다.
  - (측정) `taskkill /T`는 부모가 이미 끝난 자손에 닿지 않습니다. 그런 자손이 파이프를 쥐고 있으면 확장은 정해진 시간만
    기다리고 돌아오며 자손은 남습니다. 확장은 이를 정리 성공으로 적지 않고 stderr에 남깁니다.
  - (측정) GIL을 쥔 채 도는 C 코드 안의 주 스레드에는 그 호출이 반환할 때까지 취소가 닿지 않습니다. 요청 파일을 보는
    것이 Python 스레드이기 때문입니다. 파국적 정규식을 도는 run은 확장이 기다리는 10초 안에 멈추지 않아 강제로 끝났고
    `incomplete`로 남았습니다. POSIX에서는 C 수준 시그널 처리기가 GIL 없이 돌고 `_sre`가 시그널을 확인하므로 닿습니다.
  - (측정) SIGINT 이벤트를 보지 않는 대기(자식 프로세스를 기다리는 `subprocess` 호출 등)는 취소가 와도 그 대기가 끝날
    때까지 이어집니다. 120초 자는 자식을 기다리는 run이 10초 뒤 강제로 끝나 `incomplete`로 남았습니다.
  - (추론) Windows의 제한 시간도 Python 스레드가 SIGINT로 올리므로, 위 두 경우에 같은 이유로 닿지 않는다고 봅니다.
    실행하지 않았습니다.
  - 강제 종료는 `cancelled`가 아니라 `incomplete`입니다(D1).
  - (추론) 교체 중인 기록을 그 순간 여는 reader는 거절될 수 있고, 그러면 그 기록을 없는 것으로 읽습니다.
  - (추론) SIGINT가 무시·기본 동작인지 확인하는 것과 올리는 것은 두 호출이라, 그 사이에 스크립트가 기본 동작으로 바꾸면
    프로세스가 끝납니다.
  - aelix가 끝날 때 아직 멈추는 중인 명령은 그 뒤 강제로 끝내지 않습니다. 요청 디렉터리는 kit이 볼 수 있도록 임시
    디렉터리에 남습니다(POSIX에서는 SIGINT를 이미 보낸 상태입니다).
- 실행하지 않은 것(`not_run`). 코드가 있다는 사실을 동작 증거로 쓰지 않습니다: 네이티브 대화형 콘솔(Ctrl+C·Ctrl+Break),
  한국어 로캘 Windows, Windows의 실제 aelix 세션, 다른 사용자의 프로세스에 대한 `WindowsProcessControl.alive`, CPython
  3.11에서 aelix 프로세스가 로드하는 stdlib 부분 밖의 모든 것, Windows에서 정규식·자식 대기 중에 오는 제한 시간.
  Aelix 호스트가 Windows를 지원하더라도 이 확장의 Windows 지원으로 옮겨 적지 않습니다.
