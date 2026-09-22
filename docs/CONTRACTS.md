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
  스크립트의 stdout·stderr는 run 동안 파일로 돌려지므로 섞이지 않습니다.
- 종료 코드: 0 성공, 1 처리된 오류, 2 사용법 오류, 3 run은 기록됐으나 `ok`가 아님, 130 중단.
- 명령: `init` `status` `context` `run` `import` `query` `note` `report [--if-changed]` `demo`.
- `run`의 중단(POSIX 기준): SIGINT(uv가 전달) → `cancelled` 기록, 제한 시간 → `timeout`, SIGTERM → `cancelled`.
  Windows에서는 SIGTERM을 가로챌 수 없어 강제 종료가 `cancelled`가 아니라 `incomplete`로 남습니다. 실제 실행은 미검증입니다.

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
| 실제 동작하는 플랫폼 | 실행이 검증된 것은 POSIX뿐입니다. `fcntl`·`signal.SIGALRM`·`os.killpg`는 더 이상 최상위 import가 아니라 `_platform`의 POSIX 구현 안에 있고, Windows 구현이 같은 포트 뒤에 존재하므로 **import 단계에서 실패하지는 않습니다**. 그러나 Windows에서 실행한 적이 없으므로 동작은 `not_run`입니다 — 로드되는 것과 동작하는 것을 같은 칸에 적지 않습니다 |
| 검증된 조합 | macOS 26.6.2 arm64 / CPython 3.12.13 ([회귀 기준](BASELINE-VERIFICATION.md)). Linux는 미검증입니다 |
| 프로세스 경계 | stdlib·aelix API·`kit/labkit/core.py`와 `_platform`으로 제한되는 것은 **aelix 확장 프로세스**입니다. 확장은 `uv run --project` 자식 프로세스로 CLI를 띄웁니다(`extension/aelix_lab.py:190`). 그 CLI 프로세스는 `runner`·`data`·`report`를 자기 프로세스에서 import하고, `data.py`와 SDK가 그 안에서 duckdb·polars를 import합니다. 실행되는 스크립트도 같은 프로세스입니다 |
| 잠금·타임아웃·종료 | `kit/labkit/_platform.py`의 네 포트 뒤에 있습니다: `FileLocks`(journal·run·report 잠금), `AtomicFiles`(기록 교체), `ProcessControl`(자식 생성·생존 확인·중단·강제 종료), `RunControl`(run 제한 시간과 취소). 구현은 `detect()`가 한 번 고르고 `Project(root, platform=…)`로 전달되며, 호출부에는 `sys.platform` 분기가 없습니다. POSIX 구현은 기존 동작(`fcntl.flock`, `signal.SIGALRM`, `os.killpg`, `start_new_session`)이고, Windows 구현(`msvcrt.locking`, `threading.Timer`+`interrupt_main`, `CTRL_BREAK_EVENT`, `taskkill /F /T`)은 **한 번도 실행되지 않았습니다** |
| 파일명 | Windows 예약 장치명(`con`·`nul`·`aux`·`com1` 등)은 **새로 만드는 이름에서만** 거절합니다: `lab_import --name`과 `lab_run`이 만드는 `scripts/<name>.py`입니다. 이미 있는 이름은 거절하지 않고, run 안의 `lab.save`도 거절하지 않습니다 — 보존된 스크립트는 출력 파일이 지워진 뒤에도 다시 실행돼야 하므로, 대신 run의 `warnings`에 이식 한계를 남깁니다. 차트의 정체성(`script::id`)은 생성된 id까지 그대로 두고 저장 파일명만 바꿉니다(`con` → `con_.png`). 이 대응은 단사입니다 — 차트 id는 slug이라 `_`를 포함할 수 없으므로 `con-file` 같은 보통 id와 충돌하지 않습니다 |
| 경로·인코딩 | 리포트의 절대 경로 링크는 기록한 플랫폼 기준으로 해석해 `file:///C:/…` 형태의 URL을 만듭니다. CLI는 시작할 때 표준 스트림을 UTF-8로 고정하고, 텍스트를 읽는 파일·자식 프로세스 호출은 모두 인코딩을 명시합니다. run의 stdout·stderr는 파일 기술자와 Python 스트림을 함께 바꿔 콘솔 전용 스트림이 파일에 쓰지 못하는 문제를 피합니다 |
| 사람용 진입점 | `bin/lab`은 POSIX 셸 스크립트입니다. Windows 대응 진입점은 없습니다 |
| 배포 형태 | 루트 `pyproject.toml`이 `package = false`입니다. 설치 가능한 배포본이 없고 pack 경로에서 직접 실행합니다 |

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
- Windows 동작. 지원 플랫폼으로 채택했고 이식 가능한 경계와 Windows 구현이 코드에 있지만, Windows에서 실행한 적이 없습니다.
  잠금의 상호 배제, `uv`로의 CTRL_BREAK 전달, `time.sleep` 중 취소, 블로킹 DuckDB 호출의 제한 시간, `taskkill`의 트리 종료는 모두 미검증입니다.
  구현이 존재한다는 사실을 동작 증거로 쓰지 않으며, Aelix 호스트가 Windows를 지원하더라도 이 확장의 Windows 지원으로 옮겨 적지 않습니다.
