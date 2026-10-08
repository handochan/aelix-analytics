# Aelix Analytics 계약 v1

Aelix Analytics가 현재 **약속하는 것과 약속하지 않는 것**의 전부입니다. 코드가 기준이며,
이 문서와 코드가 다르면 코드가 맞고 문서를 고칩니다. 스키마 이름에 버전(`/1`)이 붙습니다.

## A. aelix 확장 표면 — 쓰는 것 전부

| 표면 | 쓰임 |
|---|---|
| `setup(aelix)` | 단일 파일 `extension/aelix_analytics.py`의 팩토리. 설치본에서는 같은 파일이 모듈 `aelix_analytics.extension`이고, aelix가 entry point `aelix.extensions: aelix-analytics = aelix_analytics.extension:setup`과 manifest `aelix_analytics/aelix-plugin.toml`로 찾습니다 |
| `register_flag("analytics-project", type="str")` | 프로젝트 지정 |
| `register_tool` × 6 | `analytics_status`, `analytics_query`, `analytics_import`, `analytics_run`, `analytics_record`, `analytics_init` — 모두 `execution_mode="sequential"`, 스키마는 `pattern` 없는 평범한 JSON Schema, `prompt_snippet`·지침은 줄마다 200자 이하 |
| `register_command("analytics")` | 사람 명령(`/analytics`) |
| `on("before_agent_start")` | `BeforeAgentStartResult(system_prompt=기존 + 맥락 블록)` — 그 요청에만 적용, 누적되지 않음 |
| `on("tool_execution_start" / "tool_execution_end")` | 모든 도구 호출을 `.lab/telemetry.jsonl`에 기록 |
| `on("agent_end")` | 분리된 프로세스로 `render --if-changed` 시작 |
| `events.emit` / `events.on` | 채널 `aelix-analytics:instance`로 한 runtime에 사본이 하나만 설정되게 확인 |

쓰지 않는 것: manifest의 선언형 기여(`[contributes]`: 명령·도구·hook·MCP·테마 선언), provider·login·API adapter,
메시지 렌더러, descriptor·TUI 위젯, MCP, subprocess hook, `send_message`·`append_entry` 같은 세션 조작.
manifest는 설치본이 aelix에 자기를 밝히는 데만 씁니다: `id = "aelix-analytics"`, 시작할 때 로드(`on_startup_finished`),
사실인 capability 네 개(`shell_exec`·`fs_write`·`fs_read_user`·`net` — aelix가 이 확장에는 아무것도 강제하지 않습니다),
`license = "Apache-2.0"`(배포본의 라이선스, `LICENSE`). 체크아웃의 파일 로드
(`aelix -e`·symlink)는 manifest 없이 로드됩니다.
확장은 aelix 프로세스 안에서 stdlib와 aelix API, 그리고 실행 루트(kit 디렉터리: package `aelix_analytics`를 담은 디렉터리, D)의
`aelix_analytics/core.py`와 그 형제 `aelix_analytics/_platform.py`·`aelix_analytics/relations.py`(기록을 ADR-0006대로 읽는 읽기 모델, E)
(셋 다 stdlib 전용)만 import합니다.
설치본에서는 그 앞에 package `aelix_analytics`와 `aelix_analytics._install`(실행 루트의 위치)이 import됩니다. kit 0.2.0부터 이
package가 kit 전체이지만, 그 `__init__`은 모듈 수준에서 stdlib만 import합니다: SDK 함수의 정의뿐이고 kit 모듈은 함수를 부를 때,
`core`는 `__version__`을 물을 때 import됩니다. 그래서 aelix 프로세스의 `aelix_analytics.*`는 package와 `_install`·`extension`뿐입니다.
호스트에서 SDK 함수를 부를 수는 있지만 run을 기록하는 경로가 아닙니다([ADR-0004](decisions/0004-minimal-distribution-and-entry-points.md) 결정 9).
세 kit 파일은 실행 루트의 package 디렉터리를 `__path__`로 갖는 합성 package의 submodule로 로드됩니다. package 이름에 kit 경로의 해시가 들어가고
로드가 끝나면 `sys.modules` 항목을 지우므로, 호스트 환경에 설치된 `aelix_analytics`(배포본 자신의 package)를 가리거나 두 pack이
캐시를 공유하는 일이 없습니다.
kit은 모듈 이름으로 정합니다: `aelix_analytics.extension`으로 import된 설치본은 `importlib.resources`로 찾은 자기 kit을
쓰고 `AELIX_ANALYTICS_RUNTIME_PATH`를 따르지 않습니다. 파일로 로드된 경우 `AELIX_ANALYTICS_RUNTIME_PATH`가 있으면 그것, 파일 옆에
kit이 있으면(경로로 로드한 설치본) 그것, 아니면 체크아웃의 `src/`입니다(`/analytics where`가 어느 것인지 보여 줍니다).
프로젝트는 `/analytics use` → `--analytics-project` → `AELIX_ANALYTICS_PROJECT` → 작업 디렉터리와 그 조상의 신뢰된 `lab.json` 순서로
찾습니다. `AELIX_ANALYTICS_PROJECT`는 kit CLI와 사람용 `analytics`도 작업 디렉터리보다 먼저 읽는 한 변수입니다(kit 0.3.0 전에는
CLI의 `LAB_PROJECT`와 확장의 `AELIX_LAB_PROJECT` 둘이었습니다): aelix를 위해 설정해 두면 다른 프로젝트 안에서 부른 명령도 그
프로젝트에 기록합니다.
kit 0.3.0 전의 이름(확장 파일 `extension/aelix_lab.py`, 명령 `/lab`, 도구 `lab_*`, flag `--lab-project`, 환경 변수 `AELIX_LAB_*`)은
등록하지도 읽지도 않습니다. 확장이 읽던 옛 환경 변수(`AELIX_LAB_PROJECT`·`AELIX_LAB_UV`·`AELIX_LAB_TRUST_FILE`, 파일 로드에서는
`AELIX_LAB_KIT`도)가 설정돼 있거나 aelix의 명령줄에 `--lab-project`(`--lab-project=…` 포함)가 있으면, 확장은 로드할 때 새 이름을
알리는 `RuntimeError`로 멈춥니다. aelix는 이를 확장의 로드 오류로 보이고 아무것도 등록하지 않습니다. 별칭이 아닙니다([ADR-0005](decisions/0005-names-first-storage-with-the-new-writer.md)
결정 7). aelix 0.1.0b2는 등록되지 않은 flag를 말없이 받고 `get_flag`가 그 값을 주지 않으므로, 옛 flag를 무시하면 작업
디렉터리의 프로젝트에 기록되기 때문입니다. 판정은 aelix를 시작한 명령줄(`sys.argv`)이라, 명령줄 없이 aelix를 품은 호스트는
해당하지 않습니다. 확인은 그 명령줄의 토큰을 aelix의 해석 없이 그대로 읽으므로, 다른 옵션의 값이 `--lab-project`나
`--lab-project=…`인 경우(예: `--append-system-prompt --lab-project`)에도 로드가 멈춥니다. 확장은 자기가 읽은 적 없는 옛 이름(kit이
읽던 `LAB_*`)은 보지 않고, 그 이름은 그것을 읽던 쪽이 거절합니다(D). 확장 파일을 경로로 로드할 때 kit을 찾지 못하면 이 확인보다 "kit not found"가 먼저입니다(그때 `AELIX_LAB_KIT`가
있으면 읽지 않는다고 덧붙입니다).
aelix는 세션을 만들 때마다(시작, `/new`, `/resume`, `/fork`, `/reload`) 확장을 다시 설정합니다. 파일 로드는 그때마다 새
모듈이지만 설치본의 모듈은 프로세스에 한 번 import되므로, `setup()`이 활성 프로젝트·세션 같은 상태를 매번 처음부터
시작합니다(체크아웃 경로와 같은 결과). 그래서 설치본을 업그레이드하면 aelix를 다시 시작해야 새 코드가 로드됩니다.
한 aelix runtime에는 이 확장의 사본 하나만 설정됩니다. 먼저 설정된 사본이 등록을 마친 뒤 runtime의 event bus
(`aelix.events`, 채널 `aelix-analytics:instance`)에서 응답하고, 뒤의 사본은 아무것도 등록하지 않고 stderr에 알립니다.
이 확인이 없는 이전 버전의 사본은 응답하지 않으므로 감지되지 않습니다(G).
분석 라이브러리는 전부 프로젝트 uv 환경의 자식 프로세스에서 돕니다.

### A1. 모델에 가는 텍스트의 생략과 다시 읽을 위치

맥락 블록과 도구 응답은 길이 때문에 일부를 빼거나 자릅니다. 그 자리에는 무엇이 얼마나 빠졌는지와 다시 읽을 위치를 적습니다.
위치는 정본 파일(`CONTEXT.md`의 줄 범위, `journal.jsonl`, `runs/<id>/run.json`·`stdout.txt`·`stderr.txt`,
`data/raw/<name>.meta.json`)이나 기존 도구 호출이고, 저장되지 않는 것은 그렇다고 적습니다. 맥락 블록에서 뺀 것은
`Omitted from current context`, 잘린 응답은 `truncated`/`Truncated`로 적습니다([용어](TERMINOLOGY-AND-LANGUAGE.md) 6절).

| 텍스트 | 빼거나 자르는 것 | 적는 것 |
|---|---|---|
| 맥락 블록의 CONTEXT.md 섹션 | 고정하지 않았고 4,000자 예산에 들지 않는 섹션 | 섹션마다 이름과 `read offset N limit M`. 파일 경로는 목록의 머리 줄에 한 번 |
| 맥락 블록의 머리말(첫 `## ` 위) | 4,000자 뒤 | 뺀 글자 수와 `read <CONTEXT.md> offset 1 limit <첫 제목 앞의 줄 수>` |
| 맥락 블록의 핵심 정보·열린 항목 | 최근 12개 밖의 항목, 300자 뒤의 글 | 목록마다 한 줄: 뺀 항목 수(최근 12 / 전체), 300자에서 잘린 항목이 있다는 사실, `read <journal.jsonl>`(id로 찾음) |
| `analytics_status` | 최근 run 8개, 차트·열린 항목·핵심 정보 16개, 최근 기록 10개 밖 | 목록마다 뺀 개수와 journal(run은 `runs/<id>/run.json`도). journal 경로는 `journal:` 줄에 한 번. 핵심 정보는 전체 수를 세지 않고 "최근 16개만, 더 있으면 journal"로 적습니다. 한 줄로 줄인 글 — 항목 글(열린 항목·핵심 정보 160자, 최근 기록 120자), 목표(160자), run·차트 제목(80·70자), 오류 요약(100자) — 은 `…`로 끝날 뿐 따로 적지 않습니다. 전문은 journal(id로 찾음), `CONTEXT.md`, `runs/<id>/run.json`에 있습니다 |
| `analytics_status`의 기록·관계 줄(E) | 머리의 `unsupported:`·`damaged:`·`not listed:`·`ids on several lines:` 줄에서 위치 5개(`ids on several lines:`는 id마다 줄 2개까지, 모두 5개), 데이터셋의 생산자 후보와 그 경로를 쓴 run, 그 가운데 옛 kit의 경로로만 이은 run(E, `inferred from an older kit's path: …`)에서 3개, 핵심 정보의 `refs:` 요약에서 이름을 적는 상태마다 대상 3개 밖. `refs:` 요약은 살펴볼 상태(`missing`·`ambiguous`·`content_changed`·`unsupported`)의 참조가 있는 항목에만 붙습니다 | 뺀 개수와 위치: kit의 `status` 명령이 내는 JSON(위치는 `unsupported`·`damaged`·`not_listed`·`shared_ids`, 후보와 그 경로를 쓴 run은 데이터셋의 `producer_candidates`·`written_by`, 옛 kit의 경로로만 이은 run은 `inferred`, 요약을 붙이지 않은 항목의 참조 수는 `references`에 모두 있음), 참조 대상은 journal의 그 항목 `refs`. 요약을 붙인 항목의 상태별 참조 수와 확정 연결 수는 줄이지 않습니다. 대상과 공유 id는 한 줄 80자로 줄여 `…`로 끝납니다. 이 줄들은 9,000자 예산에 듭니다 |
| `analytics_status`가 9,000자를 넘을 때 | 상한이 없는 목록 — 맥락에서 뺀 섹션 이름, view 이름, 데이터셋, 차트별 STALE 입력 — 을 줄이는 효과가 큰 것부터, 들어가는 가장 많은 개수(적어도 20·40·20·5개)로 | 뺀 개수와 위치(`CONTEXT.md`, `runs/<id>/run.json`, view 이름을 모두 보는 순서 있는 `analytics_query` 호출: `ORDER BY table_name LIMIT 50 OFFSET 0`, 이어서 `OFFSET 50, 100, …`을 50행보다 적게 올 때까지). 그래도 넘으면 줄 끝에서 자르고, 마지막 줄에 보이지 않는 절과 항목 수, journal 경로. 9,000자는 위 행의 개수·위치 줄(`journal:` 줄 포함)과 머리말이 4,000자를 넘는다는 줄을 빼고 셉니다. 그래서 그 줄들을 더해 9,000자를 넘는 것만으로는 목록을 줄이지 않고, 응답은 그 줄들만큼 9,000자보다 길 수 있습니다 |
| `analytics_run` | 입력·출력·경고 10개, 차트 12개, 출력 열 12개 밖, stdout·stderr의 꼬리 | `read <runs/<id>/run.json>`(모두 있음), `+N more columns`, 목록 밖 차트 수와 run.json, 로그는 `truncated: the last N characters`와 로그 파일. 로그가 잘렸는지는 로그 파일이 kit의 꼬리(4,000 bytes)보다 큰지로 정하고, 그때만 꼬리 앞에 kit이 붙인 `…`를 뗍니다(로그가 스스로 `…`로 시작하면 그대로). ok인 run의 1,500자 이상 stderr를 보이지 않을 때도 그 사실과 파일 |
| `analytics_query` | 14,000자에 들지 않는 행(행 단위: 줄바꿈이 든 값의 행도 한 행) | 첫 줄 `N row(s) shown of M fetched`, 마지막 줄에 뺀 행 수와 결과가 저장되지 않으니 `LIMIT/OFFSET`으로 나눠 읽으라는 안내. 셀(48자)에 맞게 잘린 값은 셀에 들어가는 48자씩 읽는 방법: `substr(<column>, <start>, 48)`, `<start>` = 1, 49, 97, … |
| `analytics_run`·`analytics_import`·`analytics_query`의 14,000자 절단 | 들어가는 마지막 줄 끝에서 자름. 그 줄 끝이 남은 길이의 절반보다 앞이면(그만큼 긴 한 줄) 그 줄 안에서 길이에 맞춰 자름 | 뺀 글자 수와 run 페이지·run.json / 데이터셋 페이지·meta.json(전체 schema) / 결과가 저장되지 않음 |
| `analytics_record`, `/analytics record`(`record gap` 포함) | 4,000자 뒤(C1) | 기록된 길이와 나머지가 journal에 없다는 사실 |
| 오류 응답 | `details`의 2,000자 뒤, `kit_failed`가 담는 stderr 1,500자 앞 | 뺀 글자 수, `the last 1,500 characters` |

줄 번호는 aelix read 도구가 세는 방식과 같습니다: 파일을 UTF-8로 읽어 `\n`으로만 나누고 1부터 세며, 앞의 빈 줄도 셉니다.
CRLF의 `\r`은 제목을 찾고 이름을 붙일 때만 떼고, 블록에 넣는 텍스트와 섹션 선택에 쓰는 섹션 길이는 줄 끝을 `\n`으로 바꾼
텍스트로 셉니다(CRLF 파일도 LF 파일과 같은 섹션이 들어감). 마지막 섹션은 파일 끝의 빈 줄을 빼고 셉니다. 단독 CR·U+2028·FF
같은 문자는 줄을 나누지 않으므로, 단독 CR로만 줄을 나눈 CONTEXT.md는 한 줄이고 전부 머리말입니다(앞 4,000자와
`offset 1 limit 1`). 이때 `analytics_status`는 줄 끝이 단독 CR이라 한 줄이라고 적고 LF나 CRLF로 저장하라고 안내합니다.

한계: 위치는 경로·줄 범위·도구 호출이고 텍스트를 만든 시점을 고정하지 않습니다. 텍스트를 만든 뒤 CONTEXT.md가 바뀌면 줄 범위가
맞지 않을 수 있고, journal과 run 기록은 그 뒤에 더해진 것도 함께 보여 줍니다. 커서·다음 페이지·부분 조회는 없습니다(#15).
50 KiB를 넘는 한 줄은 aelix read 도구가 읽지 않습니다. 블록의 생략 섹션 목록과 오류 hint는 길이 상한이 없습니다(#15).

## B. 프로젝트 파일 규약

| 경로 | 규약 |
|---|---|
| `lab.json` | 있으면 프로젝트. 위치 탐색의 표식 |
| `CONTEXT.md` | 자유 형식 Markdown. `## Goal`(옛 템플릿은 `## 목표`, Objective도) 아래에서 `(`로 시작하지 않는 첫 줄이 목표로 쓰임. 제목에 `<!-- pin -->`(옛 템플릿의 `<!-- 항상 -->`, `<!-- always -->`도)을 붙인 `## ` 섹션은 맥락 블록에 늘 들어감(A1). 새 프로젝트의 템플릿은 영어이고(#10), 기존 파일은 고쳐 쓰지 않음 |
| `journal.jsonl` | append-only. 한 줄에 이벤트 하나. 직접 고치지 않음 |
| `data/raw/<name>.parquet` | `analytics_import`(kit의 `import`)만 만든다. 같은 이름 재가져오기는 거절. 수정하지 않는 것이 규약(강제 아님) |
| `data/raw/<name>.meta.json` | 가져오기 기록 |
| `data/derived/**.parquet` | run이 쓴다 |
| `scripts/**.py` | 사람·모델이 편집하는 코드. run 입력·출력 비교에서 제외 |
| `runs/<id>/` | run이 만든 뒤 바꾸지 않는 것이 규약. `<id>` = `YYYYMMDD-HHMMSS-<script slug>[-n]` |
| `report/` | 파생물. 언제든 지우고 다시 만들 수 있음 |
| `.lab/` | `context/` 버전, `telemetry.jsonl`, `queries.jsonl`, `cache/`, `lock`(journal), `run.lock`(run 직렬화), `report.lock` |
| `.gitattributes` | 새 프로젝트(kit 0.4.0부터)에 템플릿이 넣는 `* -text`와 이유 주석: git이 이 프로젝트 파일의 줄 끝을 바꾸지 않습니다([ADR-0007](decisions/0007-line-endings.md) 결정 5). 다른 git 저장소 안의 하위 디렉터리에 만든 프로젝트에서는 그 하위 트리에 한해 상위 저장소의 줄 끝 규칙(예: `* text=auto eol=lf`)을 덮습니다. 지우면 git이 `core.autocrlf` 설정이나 상위 저장소의 규칙대로 줄 끝을 다룹니다(git 자체의 기본값은 아무것도 변환하지 않음). 기존 프로젝트에는 만들지 않습니다(#13의 전환 보고서가 제안) |
| 신뢰 목록 | 프로젝트 **밖**의 `~/.aelix/agent/lab-projects.json`(`AELIX_ANALYTICS_TRUST_FILE`·`AELIX_CODING_AGENT_DIR`로 변경; 이름은 저장 이름이라 #13까지 그대로). init과 `/analytics use`가 추가하며, 작업 디렉터리만으로 프로젝트가 활성화되려면 이 목록에 있어야 합니다 |

run의 **출력 비교 대상**은 프로젝트의 이름 공간(E의 경로 정체성)에서 `.venv .lab runs report scripts .git __pycache__ …`와
`lab.json journal.jsonl CONTEXT.md pyproject.toml uv.lock .python-version .gitignore .gitattributes`를 뺀 모든 파일입니다(`.gitattributes`는
kit 0.4.0부터). 최상위에서 앞의 이름은 디렉터리·링크·파일(워크트리의 `.git` 파일 등) 어느 것이든 뺍니다(kit 0.5.0부터). 이름 공간은 루트 아래의 트리에, 그 안에서 밖을 가리키는 링크(심볼릭 링크, junction; 디렉터리와 파일)
아래를 더한 것입니다. 안을 가리키는 링크와 루트·조상·관리 디렉터리(`runs`·`.venv`·`.lab`·`report`)로 풀리는 링크로는 내려가지
않습니다. run이 만들거나 지우거나 다른 대상으로 바꾼 링크 아래의 규칙은 G에 있습니다.

kit이 쓰는 텍스트 파일 — 기록(JSON·JSONL), 템플릿 사본과 `.python-version`, inline 스크립트, Workspace, kit이 만드는 차트 HTML·JSON,
build stamp, 데모의 `CONTEXT.md`, kit이 감싸는 run 로그(스크립트의 `print`) — 은 모든 플랫폼에서 UTF-8(BOM 없음)·LF입니다(kit 0.4.0부터,
[ADR-0007](decisions/0007-line-endings.md) 결정 2). inline 스크립트와 kit이 쓰는 HTML은 내용의 CR까지 LF로 씁니다(Python과 브라우저는 둘을 같게
읽습니다). 사용자 파일(입력·스크립트 파일, 코드 스냅샷, CONTEXT 버전, 가져온 원본의 사본, 스크립트가 쓴 파일과 바이트)은 바이트 그대로이고,
해시는 저장된 바이트의 sha256입니다(결정 3). 남은 한계는 G의 '줄 끝'에 있습니다.


**기록의 경로.** 기록이 담는 파일 경로(입력·출력·데이터셋·차트·스냅샷·로그)는 경로 정체성입니다(E): 프로젝트 안이면 이름 공간의
프로젝트 상대 POSIX 경로, 밖이면 그 기계의 절대 경로이고, URI는 받은 그대로입니다. 모든 구성 요소의 철자는 파일 시스템에 저장된
이름입니다(kit 0.5.0부터, F1). 예외는 kit이 관리하는 최상위 디렉터리 `runs`·`.venv`·`.lab`·`report`입니다: 그 아래는 다른
대소문자로 저장됐거나(`Runs`) 링크여도 kit의 이름 아래 경로(`runs/<id>/…`)입니다(E). 사람의 디렉터리(`scripts`·`.git` 등)는
저장된 이름과 실제 위치 그대로입니다.

## C. 레코드

### C1. 이벤트 `lab.event/1` (`journal.jsonl`)

| 필드 | 뜻 |
|---|---|
| `seq`, `id` | 1부터 증가하는 번호와 `e<seq>`. 프로젝트 파일 잠금(`.lab/lock`) 안에서 그때의 journal을 읽어 배정합니다: 파싱하거나 복구한 모든 줄(모르는 가족·버전·kind의 줄과 `analytics.event/2` 줄 포함)의 가장 큰 seq 다음 값입니다(ADR-0006 6절). 끊긴 줄에 글자로 남은 seq는 세지 않습니다. 그 쓰기는 끝나지 않아 id를 돌려준 적이 없고, reader는 그 줄에서 id를 읽지 않습니다. 그래서 다음 기록이 그 번호를 받을 수 있지만 같은 id의 두 기록으로 읽히지는 않습니다. 읽히는 줄이 그 번호의 id를 이미 가지고 있으면(손으로 고친 줄: seq가 정수가 아니거나(`"3"`, `3.0`, `true`) id의 번호와 다름) 그 번호를 건너뛰고 다음 번호를 받습니다. kit 0.5.0의 #49 수정 전에는 읽히는 마지막 줄의 seq 다음 값이었습니다 |
| `ts` | 로컬 시간대 ISO 8601 (밀리초) |
| `kind` | `project` `import` `run` `context` · `finding` `decision` `assumption` `question` `todo` `note` `gap` `resolve` |
| `actor` | `agent` `human` `system` `script` `test` 등 |
| `text` | 최대 4,000자. 넘는 부분은 기록되지 않습니다. 이를 알리는 것은 모델 도구(`analytics_record`)와 `/analytics record`의 응답뿐이고, SDK `analytics.record()`와 kit CLI의 `record`(`python -m aelix_analytics record`)는 알리지 않습니다 |
| `refs` | `runs[]` `charts[]`(`script::chart-id`) `data[]` `events[]` `other[]` `context`(버전 파일명) `resolves`(이벤트 id) |
| `session` | aelix 세션 파일 경로(있을 때) |

- `refs`에 짧은 차트 id(`region-revenue`)를 주면 최근 버전의 `script.py::id`로 해석해 링크합니다.
  나머지 미해석 이름만 `other[]`에 남습니다. 목록은 이벤트당 **최대 24개**(`MAX_EVENT_REFS`) —
  파일 수백 개를 읽은 run도 저널 한 줄을 경로 덤프로 만들지 않습니다. 전체 목록은 `run.json`에 있습니다.
- **핵심 정보** = `finding`·`decision`·`assumption` 중 다른 이벤트의 `refs.resolves`가 가리키지 않는 것.
- **열린 항목** = `question`·`todo` 중 같은 조건. `resolve` 종류는 `resolves`가 필수입니다.
- **덧붙이기**: 모든 이벤트(노트·가져오기·run·맥락 버전·init)는 JSON 한 줄과 LF로 덧붙입니다. journal의 마지막 바이트가 LF가
  아니면(쓰다 끊긴 줄, 줄 끝 없이 남은 기록, CRLF의 CR만 남은 줄) LF를 먼저 써서 새 기록이 따로 한 줄이 됩니다(ADR-0006 6절, #49).
  이미 있는 바이트는 고쳐 쓰지 않습니다. 끊긴 줄은 `damaged`로 보이고, 줄 끝만 없던 기록과 CR만 남은 줄은 그대로 읽힙니다. CRLF 줄은
  CRLF로 남고 새 줄은 LF입니다(ADR-0007 결정 6). 이 수정 전의 kit이 끊긴 줄 뒤에 붙여 쓴 기록과 같은 seq를 다시 받은 줄은 아래
  읽기 규칙대로 읽습니다.
- **참조의 읽기**: 화면(`analytics_status`·`/analytics status`·kit의 `status`, Workspace)은 `refs`를 고쳐 쓰지 않고 E의 참조 판정으로 보입니다. journal은 바이트로 읽어 LF로 나누고, 파싱하지 못한 줄은 `damaged`, 그 줄 뒤에 붙은 기록은 복구해 읽습니다. 같은 id의 줄이 여럿이면 줄마다 하나의 기록(`journal:<줄>`)입니다. 모르는 가족·버전·kind의 줄은 `unsupported`로 위치와 함께 셉니다(G).

### C2. run `lab.run/1` (`runs/<id>/run.json`)

| 필드 | 뜻 |
|---|---|
| `status` | `running` → `ok` `error` `timeout` `cancelled`. 기록 중 프로세스가 사라지면 읽을 때 `incomplete`로 보임 |
| `title`, `actor`, `session`, `started_at`, `ended_at`, `duration_s` | |
| `script` | `{path, sha256, snapshot}` — 실행한 바이트의 sha256과 스냅샷 경로 |
| `modules[]` | 실행 중 import된 프로젝트 안 `.py`의 `{path, sha256, snapshot}` |
| `inputs[]` | `{path, sha256, bytes, via[], external}` — `path`는 경로 정체성(E). sha256은 런타임 읽기면 **읽은 시점**, 정적 스캔·선언이면 run 시작 시점의 내용. `via`: `polars.*` `duckdb.*` `lab.read` `lab.scan` `lab.sql` `scan` `declared`. `external`은 정체성이 프로젝트 밖(절대 경로)일 때만 참입니다: 밖을 가리키는 링크 아래의 파일과 밖을 가리키는 파일 링크는 거짓 |
| `outputs[]` | `{path, change: created/modified/deleted, bytes, sha256, rows?, schema?, via: lab.save/diff, outside_data?}` — `path`는 경로 정체성, `outside_data`는 그것이 `data/`로 시작하지 않을 때 |
| `charts[]` | C3 |
| `error` | `{type, message, line, summary}` — `line`은 스크립트 안의 마지막 traceback 줄 |
| `finalize_errors` | 기록 단계(출력 수집·모듈 스냅샷·환경·journal·note 등)에서 실패한 항목. 있어도 run.json은 기록됩니다 |
| `stdout`, `stderr` | 로그 파일 경로 |
| `env` | `{python, executable, platform, packages{}, uv_lock_sha256, kit_version, kit_sha256}` |
| `rerun_of`, `reproduction` | 재실행일 때. `reproduction = {of, verdict, inputs_same, charts{}, outputs{}}` |
| `warnings[]` | 기록 품질 경고 |

입력에서 제외: 정체성이 이 run 디렉터리(`runs/<id>/`)·`.venv/`·`.lab/` 아래인 파일(그 디렉터리가 링크이거나 다른 대소문자로 저장됐어도, E), 그리고 `via`가 `scan`뿐인데 이 run이 만들거나 바꾼 파일.

기록 상태(`recording_status`)는 실행 상태와 따로 읽습니다(ADR-0006 6): run.json이 아직 `running`이거나(`not_final`), journal에 완료 이벤트(그 run을 `refs.runs`에 담은 `kind=run` 이벤트)가 없거나(`no_completion_event`), `finalize_errors`가 있으면 `incomplete`와 사유입니다. `status`의 run 행과 텍스트(`recording incomplete (…)`), run 페이지의 안내가 보입니다. run.json이 없거나 파싱되지 않는 run은 지금처럼 `(run.json missing)` 행이고, 모르는 가족·버전의 run.json은 목록에 없이 `unsupported`로 셉니다.

### C3. 차트 레코드 (run의 `charts[]`)

| 필드 | 뜻 |
|---|---|
| `id` | run 안에서 유일. 지정 id·제목의 ASCII slug, 알파벳이 없으면 `chart-<n>` |
| `kind` | `matplotlib` `plotly` `altair` `html` `file` |
| `via` | `lab.chart` `plt.show` `fig.show` `auto`(실행 끝에 열려 있던 그림) `file`(run 디렉터리에 직접 쓴 이미지·HTML) |
| `title`, `caption` | 제목이 없으면 그림에서 읽고, 그래도 없으면 id |
| `files` | `{png}` 또는 `{plotly}`(figure JSON) 또는 `{html}` 등, 프로젝트 상대 경로 |
| `data`, `data_rows`, `data_columns` | `analytics.visualize(data=…)`(kit 0.3.0 전 `chart`)로 저장한 플롯 데이터 parquet |
| `line` | 스크립트에서 차트를 기록한 줄 |

차트의 정체성은 `run.script.path::chart.id`입니다.

### C4. 데이터셋 `lab.dataset/1` (`data/raw/<name>.meta.json`)

`name, path, sha256, bytes, rows, columns[{name, dtype}], description, imported_at,
source{path, sha256, bytes, format, modified_at}`. `path`는 경로 정체성(E)이고, `source.path`는 원본의 실제 절대 경로입니다(kit 0.5.0부터
저장된 철자).

### C5. 보조 로그

- `.lab/telemetry.jsonl`: `{ts, tool, ms, error, summary, flags[], session}` — `summary`는 bash 명령 앞 300자,
  write·edit·read 경로, `analytics_*` 주요 인자. `flags`: `python_via_bash`, `wrote_managed_path`.
  `tool`은 실제로 불린 도구의 이름입니다(호스트 도구 `bash`·`read` 등 포함). 기록 종류처럼 닫힌 enum이 아니라 관측한 값이어서
  [ADR-0005](decisions/0005-names-first-storage-with-the-new-writer.md) 결정 2가 동결한 이름이 아닙니다. 그래서 kit 0.3.0부터 새 줄은
  `analytics_*`이고, 이전 세션의 줄은 `lab_*` 그대로 남습니다. 리포트의 도구 사용표는 두 이름을 각각 셉니다. 둘을 같은 도구로
  묶을지는 읽기 모델(#12)이 정합니다.
- `.lab/queries.jsonl`: `{ts, sql, rows, truncated, tables[], error?}`.
- `.lab/context/<YYYYMMDD-HHMMSS-ffffff>_<sha256 12자>.md`: CONTEXT.md 전체 사본(바이트 그대로). 새 버전마다 `context` 이벤트를 남기며 그
  글은 첫 버전이 `CONTEXT.md recorded`, 줄 끝만 바뀐 버전이 `CONTEXT.md line endings changed`(kit 0.4.0부터: 직전 버전 바이트의 sha256이 새
  바이트의 줄 끝 변형 — E의 `line_endings_only`와 같은 규칙 — 중 하나와 같을 때), 그 밖에는 `CONTEXT.md updated (+추가 -삭제 lines)`입니다.

## D. kit CLI

`uv run --project P python -m aelix_analytics --project P <command> …`. `PYTHONPATH`는 정확히 실행 루트 하나입니다: 체크아웃의
`<pack>/src`, 설치본의 `aelix_analytics/kit`(그 안에 package `aelix_analytics`의 사본이 있습니다). kit 0.2.0 전에는 모듈 이름이
`labkit`, 체크아웃의 실행 루트가 `<pack>/kit`이었습니다([ADR-0005](decisions/0005-names-first-storage-with-the-new-writer.md) 결정 7). 사람은 `bin/analytics`(체크아웃, POSIX)이나 설치본의 `analytics`(모든 플랫폼,
Windows에서는 `analytics.exe`)로 부릅니다(kit 0.3.0 전의 `bin/lab`). 두 진입점은 명령·인자·usage(바이트까지)·종료 코드를 같게 지키고 kit의
출력을 바이트 그대로 넘깁니다. 측정한 차이는 F의 사람용 진입점 행에 있습니다.

- 옛 이름의 환경 변수는 오류입니다(kit 0.3.0, [ADR-0005](decisions/0005-names-first-storage-with-the-new-writer.md) 결정 7). kit CLI는
  `LAB_PROJECT`·`LAB_ACTOR`·`LAB_SESSION`·`LAB_STYLE`·`AELIX_LAB_TRUST_FILE` 중 하나라도 (비어 있지 않게, 새 이름과 함께라도) 설정돼
  있으면 실행 가드 바로 뒤, 무엇도 쓰기 전에 `{"ok": false, "error": {"code": "env_renamed", "message": …, "hint": …,
  "details": {"renamed": {옛: 새}}}}`와 종료 코드 1로 멈춥니다. 신뢰 목록의 위치를 정하는 곳(`core.trust_file`)도 `AELIX_LAB_TRUST_FILE`에
  같은 오류를 냅니다. 사람용 `analytics`와 `bin/analytics`는 `LAB_PROJECT`·`LAB_ACTOR`·`AELIX_LAB_UV`에, 하위 명령과 `DIR`을 확인한
  뒤 kit이나 uv를 찾기 전에 stderr `analytics: <옛> was renamed <새> in kit 0.3.0 …; set <새> instead and unset <옛>`와 종료 코드
  2로 멈춥니다(두 진입점이 같은 바이트). SDK의 `project()`는 run 밖에서 `LAB_PROJECT`에 같은 문구의 `RuntimeError`를 냅니다.
  표는 `core.RENAMED_ENV`이고, 새 이름은 `AELIX_ANALYTICS_PROJECT`(둘이 하나로)·`_ACTOR`·`_SESSION`·`_STYLE`·`_TRUST_FILE`·
  `_RUNTIME_PATH`(옛 `AELIX_LAB_KIT`)·`_UV`입니다.

- 실행 루트에서만 돕니다. 설치된 배포본 자신의 package(옆에 `kit/aelix_analytics/`를 둔 것)를 `python -m aelix_analytics`로
  부르면 어느 환경에서든 아무것도 쓰지 않고 거절합니다: `{"ok": false, "error": {"code": "not_runtime_root", …}}`, 종료 코드 1.
  설치 환경에서 분석 스크립트가 프로젝트 환경 밖에서 돌지 않게 하기 위해서입니다(ADR-0005 확인 1, 소유자 결정 2026-10-06).
  실행 루트의 사본을 다른 이름으로 부른 것(`python -m aelix_analytics.kit.aelix_analytics`)도 같은 오류입니다. 스크립트의
  `import aelix_analytics`가 실행 중인 run을 보지 못하기 때문입니다. 실행 루트를 `PYTHONPATH`로 준 호출은 프로젝트 환경 밖이어도
  돕니다(진입점, 체크아웃, 3.11 하한 시험). hint는 `analytics` 명령을 알립니다. 모듈을 직접 부르려면 실행 루트를 `PYTHONPATH`에
  두고 프로젝트 환경에서 부르라고 하고(`uv run --project <project> python -m aelix_analytics ...`), 실행 루트를 출력하는
  명령(`aelix_analytics._install.kit_dir()`)도 알립니다. 판정은 무엇이 실행되는지(패키지 이름과 옆의 사본)만 보고 프로젝트 환경인지는 보지 않습니다.
- 스크립트 API는 `import aelix_analytics`입니다(예시는 `import aelix_analytics as analytics`). 함수는 `read`·`scan`·`sql`·`save`·
  `datasets`·`project`·`path`·`run_id`·`record`·`visualize`입니다. kit 0.3.0 전의 `note`·`chart`는 `record`·`visualize`가 되었고(인자와
  기록은 같습니다. `visualize`의 `via`는 `lab.chart`) 별칭이 없습니다: package의 `__getattr__`가 import 없이 새 이름을 알리는
  `AttributeError`를 내고, `from aelix_analytics import note`의 `ImportError`를 포함해 그런 run의 `error.hint`가 새 이름과, 그 run을
  그대로 다시 실행하려면 0.3.0보다 앞선 kit이 필요하다는 것을 알립니다. `labkit`이라는 이름은 없고 별칭도
  두지 않습니다. `labkit`을 import하는 스크립트와, 그런 run 스냅샷의 재실행(`run --rerun-of`)은 `No module named 'labkit'`로
  실패하고, run 기록의 `error.hint`가 새 import와, 그 run을 그대로 다시 실행하려면 0.2.0보다 앞선 kit이 필요하다는 것을
  알립니다(ADR-0005 결정 9). 스냅샷의 바이트는 고치지 않습니다.
- stdout 마지막 줄이 JSON 하나: `{"ok": true, …}` 또는 `{"ok": false, "error": {code, message, hint?, details?}}`.
  스크립트의 stdout·stderr는 run 동안 파일로 돌려지므로 섞이지 않습니다. 이 줄은 CLI가 직접 UTF-8로 인코딩해 씁니다.
  이 줄에는 줄 끝 문자가 없습니다: JSON이 문자열 안에 그대로 두는 U+2028·U+2029·U+0085는 `\u2028`·`\u2029`·`\u0085`로
  이스케이프하고(같은 JSON 값), `str.splitlines()`가 줄을 나누는 나머지 문자(`\x0b`·`\x0c`·`\x1c`–`\x1e`)는 JSON이 이미
  이스케이프합니다. 그 밖의 문자는 `json.dumps(ensure_ascii=False)`가 쓴 그대로입니다([#32](https://github.com/handochan/aelix-analytics/issues/32) 1번).
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
  명령은 JSON 한 줄만 씁니다(`demo`는 예제 스크립트를 자식 프로세스로 돌립니다). 확장은 kit의 stdout을 LF에서만 나누고(LF
  앞의 CR은 버립니다) JSON 객체로 읽히는 마지막 줄을 결과로 받습니다. 그래서 U+2028·U+2029·U+0085를 그대로 쓴 이전 kit의
  결과 줄도 온전히 읽습니다. 앞뒤 공백을 지운 뒤에도 CR이 남은 줄은 결과로 받지 않습니다: kit의 결과 줄은 문자열 안의 CR을
  이스케이프하고, 이전 확장은 단독 CR에서 줄을 나눴습니다. 결과 뒤에 스크립트가 남긴 스레드가 출력한 JSON 객체 줄은 이전처럼
  결과로 오인될 수 있습니다(이제는 세 문자를 담은 줄도 그렇습니다). kit의 데모와 저장소 도구(`tools/platform_smoke.py`)·시험
  helper는 kit의 stdout을 텍스트 모드로 읽어, CRLF와 단독 CR이 LF로 바뀐 뒤 LF에서 나눕니다. 그래서 세 문자는 거기서도 줄을
  나누지 않습니다. 데모는 `{`로 시작하는 마지막 줄을 결과로 읽고, 그 줄이 JSON이 아니면 앞의 줄로 넘어가지 않고 실패합니다.
- 종료 코드: 0 성공, 1 처리된 오류, 2 사용법 오류, 3 run은 기록됐으나 `ok`가 아님, 130 중단. 스크립트가 남긴 데몬
  스레드가 run 뒤에도 stdout에 쓰고 있으면, 인터프리터가 끝날 때 결과 줄 뒤에서 SIGABRT(POSIX에서 134)로 끝날 수
  있습니다(이전 구현과 같습니다). `print()`로 쓰거나, `sys.stdout.buffer` 또는 그것을 감싸 만든 스트림으로 쓰는 스레드가
  그렇습니다. run 동안 잡아 둔 `sys.stdout` 자체로 쓰는 스레드는 그렇지 않습니다. 다만 CLI가 자기 stdout을 되찾는
  도중에 시그널 처리기가 예외를 올리면(두 번째 Ctrl+C 등) 그런 스레드도 SIGABRT로 끝날 수 있습니다.
- 명령: `init` `status` `context` `run` `import` `query` `record` `render [--if-changed]` `demo`. kit 0.3.0 전의 `note`·`report`는
  사용법 오류(2)입니다. Workspace를 다시 빌드한 결과는 결과 JSON의 키 `workspace`에 있습니다(kit 0.6.0부터. 그 전은 `report`): `render`는
  `{path, pages, build}`(`--if-changed`가 건너뛰면 `{path, skipped: true}`), `import`·`record`는 같은 값이거나 빌드가 실패하면 `{error}`,
  `demo`는 같은 값입니다. `run`은 같은 값이거나, 빌드가 실패하면 그 글을 키 `workspace_error`(그 전은 `report_error`)에 담습니다.
  `status`의 `workspace`(그 전은 `report`)는 Workspace의 `index.html` 경로입니다. 값의 경로는 B의 저장 이름 `report/` 그대로입니다.
- `status`의 JSON(`status`)은 한 번의 읽기(E의 읽기 모델)에서 만듭니다: 읽기 기준 `as_of_seq`·`source_revision`·`updating`, `unsupported`(`{location, reason}`), `damaged`(`journal`·`recovered`: `journal:<줄>`, `files`: `{location, reason}`), `not_listed`(`analytics.event/2` 줄), `shared_ids`(여러 줄의 id → 줄), run 행의 `recording_status`·`reasons`, 차트 행의 `ambiguous_inputs`·`cycle`, 데이터셋의 `producer_state`·`producer_candidates`·`producer`(후보가 하나일 때만)·`written_by`·`unsupported_record`(가져오기 기록이 이 kit이 읽지 못하는 가족·버전일 때 `{location, reason}`. 그 기록의 `rows`·`sha256`·`description`·`source`·`imported_at`은 쓰지 않습니다), 목록 항목의 `source`(id, 여러 줄이면 `journal:<줄>`), 핵심 정보의 `references`(`states`·`confirmed`·`not_resolved`), 이름이 대소문자만 다른 데이터셋 묶음 `case_only_datasets`(`[[{name, path, view}]]`, kit 0.5.0), 옛 kit 경로의 추론 대응(E "옛 kit의 경로"): 차트 행의 `inferred_versions`(수)·`inferred_inputs`(입력 경로)·`inferred_upstream`(stale 후보 run), 데이터셋의 `inferred`(`producer_candidates`·`producers`·`used_by`·`written_by` 가운데 그렇게만 이어진 run, 있을 때만). 이전 kit(0.2.0·0.3.0)의 `unreadable`(가족·버전별 개수)은 없어졌고, `datasets[].producer`의 뜻이 "그 경로를 출력한 마지막 성공 run"에서 "현재 바이트를 출력한 유일한 성공 run"으로 좁아졌습니다. `producers`는 그대로 그 경로를 출력한 성공 run(바이트 무관)입니다. 배포본을 게시하지 않았으므로 외부 소비자가 없습니다(ADR-0005 결정 8).
- `run`이 코드 스냅샷이나 첫 기록(`running`)을 쓰지 못하면 만든 run 디렉터리를 지우고 `snapshot_failed`·`record_failed`로
  답합니다. hint는 원인에 따라 경로를 줄이라는 것(운영체제가 이름이나 경로가 너무 길다고 답했거나, 경로의 한 구성
  요소가 UTF-16 단위로 255개를 넘음: NTFS·APFS가 이름을 세는 단위이며 기본 다국어 평면 밖의 문자는 둘로 셉니다),
  디스크 공간, 제목·스크립트 이름·`AELIX_ANALYTICS_ACTOR`·`AELIX_ANALYTICS_SESSION`을 UTF-8로 쓸 수 있는 값으로 하라는 것, `runs/` 쓰기 권한 중 하나입니다. run 디렉터리를 만들기 전(잠금, `runs/` 자체)과 첫 기록 뒤(로그 열기)의 실패는
  이전과 같이 traceback으로 끝나며, 첫 기록 뒤라면 `running` 기록이 남아 `incomplete`로 보입니다. UTF-8로 쓸 수 없는
  문자를 담은 입력·출력 경로가 마지막 기록을 쓰지 못하게 하는 경우와, 그런 `AELIX_ANALYTICS_SESSION`으로 바뀐 맥락을 동기화하는
  경우도 그렇습니다(G).
- `AELIX_ANALYTICS_CANCEL_FILE`(kit 0.3.0 전 `LAB_CANCEL_FILE`): 이 경로에 파일이 생기면 명령을 중단합니다(D1). 확장과 kit 사이의
  내부 프로토콜이고 사용자가 정하는 설정이 아닙니다. 확장은 Windows에서만 넘기고, kit은 시작할 때 이
  변수를 환경에서 지워 스크립트의 자식 프로세스가 같은 파일을 보지 않게 합니다.
- run 동안 kit은 스크립트에 `AELIX_ANALYTICS_RUN_ID`·`AELIX_ANALYTICS_RUN_DIR`·`AELIX_ANALYTICS_PROJECT`를 줍니다(kit 0.3.0 전
  `LAB_RUN_ID`·`LAB_RUN_DIR`·`LAB_PROJECT`). `AELIX_ANALYTICS_ACTOR`·`AELIX_ANALYTICS_SESSION`이 기록의 `actor`·`session`이 되고,
  `AELIX_ANALYTICS_STYLE=0`이면 matplotlib·plotly 기본 스타일을 끕니다.

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
| 취소 | 확장이 `uv run` 자식에 SIGINT를 보내고 uv가 kit에 전달. SIGTERM도 취소 | 확장이 임시 디렉터리에 요청 파일을 만들고(`AELIX_ANALYTICS_CANCEL_FILE`), kit의 감시 스레드가 주 스레드에 SIGINT를 올림(`signal.raise_signal`). 콘솔의 Ctrl+C(SIGINT)와 Ctrl+Break(SIGBREAK)도 취소로 받습니다: 설치본 `analytics`의 run에 의사 콘솔(ConPTY)과 고전 콘솔에서 보내 `cancelled`로 기록됐습니다(측정 — Ctrl+C는 의사 콘솔의 `\x03` 입력과 고전 콘솔에 붙은 도우미 프로세스의 `GenerateConsoleCtrlEvent`, Ctrl+Break는 도우미의 `GenerateConsoleCtrlEvent`와 의사 콘솔의 win32-input-mode 입력열). 사람이 보이는 콘솔 창에서 누른 키는 실행하지 않았습니다. Ctrl+Break가 uv를 먼저 끝내는 것은 G |
| 제한 시간 | `setitimer`와 SIGALRM. kit이 run 전체 동안 SIGALRM 처리기를 점유하고, 구간이 끝나면 그 구간이 켠 타이머를 끕니다 — 스크립트가 바꿔 놓은 주기 타이머도, kit의 중단을 잡은 스크립트가 그 뒤에 건 타이머도(이전 구현처럼 ITIMER_REAL을 끔). 구간이 타이머를 켜지 못했으면(`--timeout inf`) 호스트 프로세스의 타이머는 건드리지 않습니다(이전 구현은 껐습니다) | 타이머 스레드가 제한 시간을 표시한 뒤 SIGINT를 올림 |
| 강제 종료 | 확장이 10초 기다린 뒤 프로세스 그룹에 SIGKILL | 확장이 10초 기다린 뒤 `taskkill /F /T`. Windows에서는 SIGTERM을 가로챌 수 없어 강제 종료가 `cancelled`가 아니라 `incomplete`로 남습니다 |

스크립트가 이 시그널에 자기 처리기를 설치하면 kit 대신 그 처리기가 받습니다. 그 경우와 한 번만 중단하는 규칙의 비용,
Windows의 한계는 G절에 있습니다.

## E. 계산 규칙

| 대상 | 규칙 |
|---|---|
| 입력 기록 | 런타임 읽기는 **읽는 시점의 내용**으로 해시합니다(그때 판정이 닿은 파일을 엽니다. 링크를 거친 판정은 기억하지 않고 스크립트가 시작할 때 그때까지의 판정과 디렉터리 목록을 버리므로, run이 다른 대상으로 바꾼 링크(`latest → v4`)는 읽는 때의 대상입니다). run이 스스로 만든 파일, 또는 다시 쓴 뒤 읽은 파일은 입력에서 제외합니다. 디렉터리를 읽으면 그 디렉터리에서 시작한 이름 공간 조회(아래)가 찾는 순서로 처음 500개의 데이터 파일을 기록하고 조회는 거기서 멈춥니다(이름으로 빼는 디렉터리는 없습니다) |
| 경로 정체성 | ADR-0006 9절. 경로를 절대 경로로 만든 뒤(Windows는 Win32처럼 `.`·`..`를 글자로 접은 뒤) 앞에서부터 구성 요소 단위로 판정합니다: 루트에 닿기 전의 링크는 풀고, 루트 안에서 안을 가리키는 링크는 실제 위치로 풀며, 밖을 가리키는 링크(루트·조상·최상위 관리 디렉터리의 실제 위치로 풀리는 것 제외)는 그 자리를 경계로 삼습니다. POSIX에서 링크 뒤의 `..`는 링크 대상의 부모입니다. 끝이 루트 안이면 프로젝트 상대 경로입니다. 단 kit이 관리하는 최상위 디렉터리(`runs`: run 기록·스냅샷·차트, `.venv`: uv가 두는 프로젝트 환경, `.lab`: kit의 상태, `report`: kit이 만드는 리포트)는 링크면 그 실제 위치 아래가, 다른 대소문자·정규화로 저장됐으면(같은 디렉터리 항목인 `Runs`) 그 아래가 kit의 이름 아래 경로(`runs/<id>/…`)입니다. 저장된 철자의 유일한 예외이며, 그래서 입력 제외(`runs/<id>/`·`.venv/`·`.lab/`), 차트 정리, 모듈 스냅샷의 제외가 링크와 철자에 관계없이 듭니다. 그 밖의 제외 이름(`scripts`·`.git` 등 사람의 디렉터리)은 이 대응이 없어, `scripts → src`의 `scripts/a.py`는 `src/a.py`입니다. 경계 아래면 이름 공간 조회가 그 파일을 낸 경로, 그 밖이면 실제 절대 경로입니다. **조회**는 루트의 트리(최상위의 제외 이름 — 디렉터리든 링크든 파일이든, 다른 대소문자로 저장된 관리 디렉터리 포함 — 와 어디서나 `__pycache__`·`.ipynb_checkpoints` 제외)를 걸은 뒤 경계를 이름 공간 경로의 코드 포인트 순으로 대상마다 한 번 걷습니다: 같은 디렉터리는 파일 키 `(st_dev, st_ino)`(`st_ino`가 0이면 실제 경로)로, 같은 파일은 부모의 키와 이름으로 알아보고, 이미 걸은 것은 다시 내지 않습니다. 그래서 한 파일에 경계 여럿으로 닿으면 코드 포인트 순으로 가장 앞인 경계 아래의 경로입니다(`data/b/x.csv`로 읽은 입력과 `data/a/` 아래의 출력이 같은 `data/a/x.csv`). 조회가 걷지 않은 경계(최상위 제외 디렉터리 안, 조회 뒤에 생긴 링크)만 그 경로가 건넌 경계 가운데 가장 앞인 것을 씁니다. 끊긴 링크와 링크 고리는 따라가지 않습니다. **철자**: 구성 요소마다 부모 디렉터리 목록의 같은 이름, 없으면 NFC+casefold가 같은 이름 가운데 코드 포인트 순으로 처음 같은 디렉터리 항목인 것, 목록을 읽을 수 없거나 그런 이름이 없으면 입력한 대로입니다. 프로젝트 루트와 신뢰 목록의 철자도 같습니다. 프로젝트 하나 밖에서(프로젝트를 찾을 때, 신뢰를 물을 때, 루트를 정할 때) 정한 이름은 프로세스가 구성 요소마다 기억하고, 그 이름을 담은 디렉터리의 수정 시각이 그대로이면 그 디렉터리를 다시 읽지 않습니다(바뀐 디렉터리만 다시 읽음). 디렉터리의 수정 시각으로 목록을 다시 읽을지 정하는 곳(이것과 run 중의 목록)은 바뀐 지 2초가 안 된 디렉터리의 시각을 믿지 않습니다: 파일 시스템은 변경을 시계 틱(Windows 15.6 ms)이나 거친 시각(FAT 2초)으로 적어, 곧이은 변경이 시각을 바꾸지 않을 수 있습니다. 경로를 루트 아래로 판정할 때는 루트에서 시작해 루트의 상위 디렉터리를 읽지 않습니다. **시작 위치를 준 조회**(데이터셋 목록, 디렉터리 읽기, 리포트의 `data/` 지문)는 그 디렉터리부터 같은 규칙으로 깊이 우선, 디렉터리마다 이름의 코드 포인트 순으로 걷고(보통 디렉터리의 트리에서는 경로의 구성 요소별 코드 포인트 순), 안을 가리키는 파일 링크도 그 대상의 정체성으로 내며, 같은 (이름, 정체성)은 한 번 냅니다. 시작 위치가 루트나 그 조상으로 풀리면(`data/raw → /`) 링크가 그리로 풀릴 때처럼 아무것도 걷지 않고, 시작 위치의 실제 디렉터리나 그 위로 돌아가는 링크(`derived/up → ..`)도 따라가지 않습니다. **보안 판정**(스크립트 위치, 재실행의 스냅샷·스크립트 경로, 모듈 스냅샷 대상, 질의 샌드박스)은 정체성이 아니라 철자를 맞춘 실제 경로로 합니다 |
| 데이터셋 목록 | `data/raw`·`data/derived`에서 시작한 조회가 찾은 `.parquet` 파일(접미사 대소문자 무관). 이름은 조회가 만난 항목(파일 링크면 링크 자신)의 stem, 경로는 정체성이고, 가져오기 기록(`<stem>.meta.json`)과 그 `unsupported_record.location`은 그 항목 옆의 것입니다(파일을 옮기고 링크로 둔 `data/raw/sales.parquet`도 기록을 잃지 않음). 같은 (이름, 정체성)은 종류마다 한 번이라, derived의 링크가 raw 파일을 가리키면 두 종류에 다 있습니다(view `sales`·`sales_derived`). raw 다음 derived, 종류마다 정체성의 구성 요소별 코드 포인트 순(같으면 이름 순)입니다 |
| 입력 상태 | 기록 sha256 대비 지금 파일: `same` / `changed` / `missing` / `unknown`(sha 없음·디렉터리·URI). 지금 내용이 그 run 자신의 출력과 같으면 `same`(읽고 다시 쓰는 갱신 패턴). 크기·mtime이 같으면 캐시한 sha를 씀. `changed`인 파일이 64 MiB 이하이고 앞 8 KiB에 NUL이 없을 때, 지금 바이트의 CRLF를 LF로 바꾼 것이나 그 결과의 모든 줄 끝을 CRLF로 바꾼 것의 sha256이 기록과 같으면 사유 `line_endings_only`를 붙입니다(kit 0.4.0부터, [ADR-0007](decisions/0007-line-endings.md) 결정 4). 상태·stale·해시는 바이트 그대로입니다. JSON은 입력의 `reason`과 status 차트 행의 `change_reasons`(`{경로: "line_endings_only"}`), `analytics_status`·kit `status`의 글은 STALE 사유의 경로 뒤 `(line endings only)`, Workspace는 `Line endings only` 배지와 경로 뒤 `(line endings only)`입니다. 두 변형의 해시는 해시 캐시의 그 파일 항목에 함께 두며, 읽기 전후의 크기·mtime이 같을 때만 둡니다(읽는 동안 바뀐 파일은 사유 없음). 판정하지 않는 파일(64 MiB 초과·앞 8 KiB의 NUL)은 그 크기·mtime에 '판정 안 함'을 두고, 읽지 못한 파일은 사유 없이 아무것도 두지 않아 다음에 다시 읽습니다 |
| stale | 입력 중 `changed`·`missing`이 있거나, 입력의 생산자 후보 가운데 stale인 run이 있음(전이). 후보를 모두 따라가고 마지막 run을 고르지 않습니다. 후보가 둘 이상인 입력은 `ambiguous_inputs`, 따라가다 아직 걷는 중인 run으로 돌아오면 `cycle`이며(같은 시각에 시작했거나 시작 시각을 읽지 못한 run 사이에서만 생깁니다. 두 길로 닿는 다이아몬드는 순환이 아님) 깊이 제한 없이 run마다 한 번 방문해 끝납니다. 화면은 stale인 후보를 생산자로 단정하지 않습니다: `analytics_status`는 `STALE(upstream candidate(s) …)`, Workspace는 입력이 직접 바뀐 run에만 "This run's inputs differ from the files now."를 적고, 후보만 stale이면 "Upstream candidate stale"(같은 바이트를 출력한 run의 입력이 바뀜)을 적습니다 |
| 생산자 후보 | 입력의 `(path, sha256)`과 같은 출력(`deleted` 아님)을 남긴 `ok` run 가운데, 소비 run이 아니고 그보다 늦게 시작하지 않은 것(시작 시각은 시간대를 반영한 시점, 읽지 못하면 빼지 않음). 0개 `none`, 1개 `resolved`, 여럿 `ambiguous`(모두 보임), 입력에 해시가 없으면 `unknown`. 데이터셋은 현재 바이트로 같은 판정을 합니다(소비 run 없음). 후보는 같은 바이트를 출력했다는 관측(basis `observed`)이며 확정 연결이 아니고, 화면은 `same bytes as run X's output`(여럿이면 `same bytes as N runs: X, Y`)으로 적습니다(ADR-0006 4). 데이터셋 페이지는 후보가 있을 때만 "그 run이 이 경로에 이 바이트를 출력했다"고 적고, 그 경로를 출력으로 기록한 성공 run은 바이트와 무관한 목록("Successful runs that recorded an output at this path", 지운 것은 `deleted this path`)으로 따로 적습니다. 옛 kit의 경로로만 맞은 후보는 추론입니다(아래 행) |
| 참조 판정 | ADR-0006 3·4절대로 읽습니다: `/1` `refs`의 정규화(4절 끝의 목록), 해석 상태, `confirmed`(author ∧ observed/declared ∧ resolved). 이벤트 계열 참조(`/1`의 `refs.events`·`refs.resolves`, `/2`·Report manifest의 결박된 `event`·`analysis_focus`·`metric_definition`·`reuse_proposal`)는 대상의 seq가 참조한 기록의 seq보다 작을 때만 찾고, 같거나 크거나 seq가 없으면 `missing`(`record_absent`)·`confirmed=false`입니다. ADR-0006 4절 2단계("대상 기록을 찾는다")의 읽기입니다(G). `/1` `refs.runs`는 run의 `started_at`이 이벤트 `ts`보다 늦으면 `missing`, `kind=run` 이벤트의 `refs.charts`는 `refs.runs`가 하나일 때 그 run의 버전에 결박되고(여럿이면 `ambiguous`), 그 밖의 `refs.charts`·`refs.data`는 `version_unbound`입니다. 미결박 차트의 후보는 그 이벤트 앞의 완료 이벤트를 가진 성공 run의 버전이고, 그 가운데 옛 kit의 경로로만 그 키의 버전인 것은 `inferred_candidates`에도 있습니다(아래 행) |
| 참조 표시 | run 이벤트의 차트는 그 run 페이지의 차트(`runs/<run>.html#chart-<id>`), 미결박 차트·데이터는 "Version unbound"와 함께 최신 갤러리 카드·데이터셋 페이지로 잇고 "latest, not the cited version"과 후보 수를 적습니다. 그 밖의 상태(`missing`·`ambiguous`·`unknown`·`unsupported`·`content_changed`)는 색이 아니라 글로 적습니다. 타임라인의 해결 배지는 C1의 선택을 따르되, 닫는 기록의 `refs.resolves` 판정이 `resolved`일 때만 "✓ Resolved by e3"이고 아니면 그 상태를 적습니다(`closed by e3 · Missing: record_absent`). `analytics_status`의 핵심 정보는 살펴볼 상태(`missing`·`ambiguous`·`content_changed`·`unsupported`)의 참조가 있을 때 상태별 수와 확정 연결 수, 그 상태의 대상 식별자를 적습니다. 참조가 모두 `resolved`·`version_unbound`·`unknown`인 항목은 아무것도 붙이지 않고 수는 JSON의 `references`에 있습니다(`/1` 메모의 차트·데이터는 원래 미결박입니다). 식별자는 두 화면이 같습니다: 이벤트 `e<seq>`(여러 줄이면 `journal:<줄>`), run id, `<run_id>/<chart_id>`, 논리 키 `script::chart`, 경로, 읽지 못하는 참조는 기록 안의 자리(`/1` `refs.<field>`, `/2` `refs[<index>]`). 여러 줄의 id를 가리키는 이벤트 참조의 후보는 기록(`candidate record(s)`), 미결박 차트의 후보는 버전입니다 |
| 읽기 기준 | `analytics_status`의 `read:` 줄과 Workspace의 모든 페이지 바닥이 같은 `as_of_seq`(journal의 마지막 seq)와 `source_revision`(journal·run.json·manifest·meta·CONTEXT의 크기와 mtime으로 만든 16자, 절대 경로 없음)을 적습니다. 읽는 동안 파일이 바뀌면 두 번까지 다시 읽고, 그래도 바뀌면 `updating`이라고 적습니다(ADR-0006 5) |
| 출력의 현재 상태 | run 페이지는 출력마다 지금 파일을 기록한 sha256과 비교합니다: 같음, 다름("Overwritten since this run — this run's bytes are not preserved"), 없음(`file_absent`), 해시 없음(`unknown`) |
| 코드 상태 | 원래 스크립트 파일 sha256 대비 `same` / `changed` / `missing` (재실행은 `n/a`). `changed`인 코드도 입력 상태와 같은 규칙으로 사유를 붙입니다: JSON `code_reason`(`line_endings_only`, 없으면 `null`), 글 `script edited since (line endings only)`, Workspace의 `Line endings only` 배지와 코드 상태 `Current file edited (line endings only)` |
| 차트 정체성 | run 안에서는 차트 id가 정체성입니다. 같은 id로 다시 기록하면 나중 것이 이깁니다. 자동 캡처(`plt.show`·`fig.show`·남은 그림)는 이미 저장한 그림이 그대로면 건너뜁니다 |
| 차트 버전 | `script::id`별 성공 run 순서(재실행 run 제외). 같은 스크립트의 최신 성공 run에 없는 차트는 `retired`. 갤러리와 `status`의 차트 목록의 규칙입니다. 참조 판정의 버전 후보는 ADR-0006대로 재실행 run도 셉니다(완료 이벤트가 있는 성공 run의 버전). kit 0.5.0 전 run의 스크립트 경로는 아래 "옛 kit의 경로"로 읽어 지금 키의 버전이 되고(추론), 그 run이 기록한 키(`Scripts/Plot.py::trend`)를 가리키는 참조와 링크도 지금 키(`scripts/plot.py::trend`)로 읽습니다 |
| 옛 kit의 경로 | kit 0.5.0(`core.PATH_IDENTITY_SINCE`) 전의 run — `env.kit_version`이 PEP 440 순서로 그보다 낮거나, 없거나, ADR-0006 1.3의 형식이 아니거나 Python이 정수로 읽지 못할 만큼 긴 숫자를 가진 run — 이 기록한 입력·출력·스크립트 경로는 ADR-0006 9절의 추론 대응으로 읽습니다([2026-10-06 소유자 결정](https://github.com/handochan/aelix-analytics/issues/12#issuecomment-6009076270)). 그런 kit은 링크를 끝까지 푼 실제 경로를, 루트 안이면 루트 기준 상대 경로로, 이름은 입력한 철자로 기록했습니다. `Project.legacy_identity`가 루트나 링크 대상 아래의 절대 경로와 저장된 철자가 아닌 상대 경로를 지금의 정체성 경로(위 "경로 정체성"의 철자·링크 규칙)로 대응시키고(그런 kit이 쓸 수 없던 상대 경로 — 빈·`.`·`..` 구성 요소, 역슬래시, 드라이브(`C:x`), URI — 는 대응하지 않음), 읽기 모델은 그 대응이 기록과 다르고 지금 있는 항목이거나 어떤 run이 출력으로 기록한 경로일 때만 씁니다(지어내지 않음). 그렇게만 맞은 생산자 후보·차트 버전·데이터셋의 run은 추론(`basis: inferred`)이며 관측(`observed`)이나 확정 연결로 세지 않습니다: 생산자 판정의 `basis: inferred`·`inferred_candidates`·`read_as`, 참조 판정의 `inferred_candidates`, status JSON의 `inferred*`(D). 0.5.0 이후 run도 기록한 글자(입력·출력·스크립트 경로)는 옛 run의 것도 대응하지 않고 기록 그대로 읽습니다: 그 글자는 그때의 정체성 경로였으므로, 그 뒤 이름을 바꾸거나 링크를 새로 만들어 지금 가리키는 것이 달라져도 그 글자를 기록한 run들은 이전처럼 글자로 이어집니다. 그래서 기록의 글자가 같은 어느 두 경로든 관측 그대로입니다. 후보가 여럿이면 `ambiguous` 그대로입니다. 다만 Visualization id만 적은 참조(`token`)의 키가 한 스크립트의 옛 철자와 지금 철자뿐이면 대응으로 한 키가 되어 `version_unbound`이고 옛 철자의 버전은 `inferred_candidates`에 있습니다(대응하지 않으면 `ambiguous`). 0.5.0 이후 run의 경로는 기록 그대로 읽고, 기록은 고쳐 쓰지 않습니다. `env.kit_version`이 없는 run 기록은 아직 끝나지 않은 run이나 환경을 기록하지 못한(`finalize_errors`의 `environment`) 0.5.0 이후 run이라도 옛 kit의 것으로 읽습니다. 그런 run의 경로는 이미 정체성 경로라 대응해도 같은 경로이고, 그 뒤 이름·링크가 바뀐 경우에만 다른 경로로 대응하며 그때도 추론으로 표시합니다. 화면: `analytics_status`·kit `status`의 글은 `inferred from an older kit's path`(데이터셋의 후보와 그 경로를 쓰거나 읽은 run, 차트의 버전 수·입력 수·stale 후보), Workspace는 `Inferred from an older kit's path` 배지, run 페이지 입력·출력 경로 옆의 `read as <경로> — inferred from an older kit's path`, 데이터셋 페이지 목록의 run 옆 `inferred from an older kit's path`, 참조의 후보 수 옆의 수, 이전 버전의 `(inferred)`입니다. 경로·키는 사용자 내용이라 `lang=""` 안에 둡니다 |
| view 이름 | 파일 stem을 `[a-z0-9_]`로. 숫자로 시작하면 `t_`. kit의 데이터셋 이름 규칙(`^[a-z][a-z0-9_]{0,62}$`, `import`·`analytics.save`가 주는 이름)에 맞는 stem이 종류와 무관하게 먼저 이름을 받고, 그 안에서 raw 우선, 그다음 경로 순입니다. 겹치는 derived는 `_derived`, 그래도 겹치면 `_2`·`_3`…입니다. 그래서 사람이 둔 `data/raw/Sales.parquet`은 kit의 `sales`를 가져가지 않습니다. 이름이 대소문자(또는 유니코드 정규화)만 다른 데이터셋 묶음은 `status`가 경로·view와 함께 경고합니다(대소문자를 무시하는 파일 시스템은 그 가운데 하나만 담습니다). 대소문자가 아닌 겹침(`sales-2`와 `sales_2`)은 말없이 `_2`입니다 |
| 재실행 판정 | 재실행이 `ok`가 아니면 `failed`. 차트: 둘 다 플롯 데이터면 프레임 동등성, plotly면 `data` JSON 동등성, PNG면 바이트. 출력: 바이트. 다르거나 사라지거나(`not written`) 새로 생기면 `mismatch`, 같은 것이 있으면 `match`, 비교할 것이 없으면 `inconclusive`. 입력 동일 여부는 따로 `inputs_same` |
| 읽기 전용 쿼리 | 한 문장이어야 하고 종류가 SELECT(DESCRIBE·SUMMARIZE·SHOW·PRAGMA 포함)여야 합니다. `EXPLAIN`은 안쪽이 SELECT일 때만, `EXPLAIN ANALYZE`는 거절. 연결은 `allowed_directories=[<project>/data/]` + `enable_external_access=false` + `lock_configuration=true`로 묶습니다 |
| SQL 계보 | DuckDB 파서(`json_serialize_sql`)로 참조 테이블을 찾고 CTE 이름은 그 범위 안에서만 제외합니다. 파서가 받지 않는 문장은 FROM·JOIN 뒤 이름만 맞춰 보는 대비책을 씁니다 |
| 프로젝트 활성화 | `/analytics use`·flag·env는 명시적 선택입니다. 작업 디렉터리로만 찾은 프로젝트는 신뢰 목록에 있어야 활성화되고, 아니면 `project_untrusted`로 거절하며 맥락 주입·telemetry도 하지 않습니다 |

## F. 실행 환경과 지원 플랫폼

지원 플랫폼은 Linux·macOS·Windows입니다([ADR-0002](decisions/0002-python-support-and-platform-scope.md), 2026-09-21 채택).
이식 가능한 경계의 구현 방식은 [ADR-0003](decisions/0003-portable-boundaries-and-in-process-kit-loading.md),
배포본·진입점·설치 자원은 [ADR-0004](decisions/0004-minimal-distribution-and-entry-points.md)입니다.
아래 표는 **현재 코드가 실제로 하는 것**이며, 채택한 목표와 구별합니다.

| 항목 | 현재 코드 |
|---|---|
| 선언된 Python 하한 | 배포본(`pyproject.toml`의 `requires-python`, wheel의 `Requires-Python`)은 `>=3.11`입니다. 이 하한은 설치 환경에서 도는 것 — `analytics` 런처, aelix 확장, 확장이 로드하는 `core`·`_platform`·`relations` — 의 것입니다. 개발 환경(dev dependency group, `[tool.uv.dependency-groups]`)과 분석 프로젝트 환경(`src/aelix_analytics/template/pyproject.toml`)은 `>=3.12`이고, kit의 명령은 프로젝트 환경에서 돕니다. `uv.lock`의 고정 버전은 바뀌지 않았고 dev group은 3.11에서 sync되지 않습니다. 하한 판정([측정 기록](verification/2026-10-02-issue-8-windows-inventory.md)): `>=3.11`은 설치 환경에서 도는 것에 대해 성립합니다 — 세 플랫폼에서 CPython 3.11·3.12에 설치, CPython 3.11에서 aelix 0.1.0b2 통합, 표준 라이브러리만으로 aelix 프로세스가 로드하는 부분(3.11), 런처 시험(3.11), Windows의 3.11 설치본으로 콘솔 Ctrl+C·Ctrl+Break. 분석 프로젝트는 `>=3.12`로 남습니다. D1의 중단 보장은 kit이 도는 3.12 이상에서 측정했고, 3.11에서는 분석 라이브러리를 쓰지 않는 스크립트의 `run`이 실행되고 기록되는 것만 확인했습니다 |
| 실행으로 확인한 범위 | 이식 가능한 경계(PR-1)는 Ubuntu 24.04와 Windows Server 2025(GitHub Actions), macOS 26.6.2 arm64(개발 기기)에서 실행했습니다([측정 기록](verification/2026-09-24-issue-8-platforms.md)). 배포본(PR-2)도 같은 세 플랫폼에서 wheel을 빌드해 체크아웃 밖의 새 CPython 3.11·3.12 환경에 설치하고 `tests/test_packaging.py`(end-to-end 단계 포함)로 확인했습니다([측정 기록](verification/2026-09-30-issue-8-distribution.md)). Windows 인벤토리(링크·긴 경로·대소문자·POSIX를 가정하던 시험·콘솔·두 프로젝트 격리)는 저장소 CI(`.github/workflows/ci.yml`)가 Ubuntu 24.04·Windows Server 2025에서 PR마다, macOS 26에서 라벨로 실행했고 개발 기기 macOS에서도 실행했습니다(콘솔과 긴 경로의 시험은 Windows에서만 돕니다)([측정 기록](verification/2026-10-02-issue-8-windows-inventory.md)). 측정한 Windows 구성은 하나입니다: Windows Server 2025 Datacenter(GitHub 호스트 이미지 `windows-2025`, ImageOS `win25-vs2026` 20260925.250.1), build 26100 x64, 관리자 계정(High integrity는 조사 실행에서 읽음), Developer Mode 켬, LongPathsEnabled=1(긴 경로 시험은 0으로도). 클라이언트 판(같은 build의 Windows 11 24H2 포함), 표준 사용자 토큰, 기본 설정의 Windows는 실행하지 않았습니다. 줄 끝(kit 0.4.0, #42)은 저장소 시험(`tests/test_line_endings.py`, wheel 시험, 배포본 end-to-end)과 aelix 통합 시험이 레그마다 같은 상수(바이트·sha256, git blob에서 계산한 `kit_sha256`)를 단언해 확인합니다([측정 기록](verification/2026-10-06-issue-42-line-endings.md)이 레그별 결과와 실행하지 않은 것을 적습니다). Windows에서 확인한 것, 선언한 한계, 실행하지 않은 것은 G절에 있습니다. 실행하지 않은 조합으로 지원을 넓혀 적지 않습니다. 경로 정체성(kit 0.5.0, #36)은 [측정 기록](verification/2026-10-06-issue-36-path-identity.md)에 있습니다 |
| 검증된 조합 | 마지막 측정([측정 기록](verification/2026-10-02-issue-8-windows-inventory.md), `4eb0955` 커밋): 저장소 CI의 Ubuntu 24.04 x64(CPython 3.12.3·3.11.15), Windows Server 2025 x64(3.12.10·3.11.9), macOS 26.6.2 arm64 호스트 러너(`macos-26`, 3.12.10·3.11.9)에서 저장소 시험(배포본 end-to-end 포함)·플랫폼 스모크·aelix 0.1.0b2 통합(3.12·3.11, 분석 라이브러리가 없는 격리 환경)·런처 시험(3.11), `actions/setup-python`의 CPython 3.11(Ubuntu 3.11.16, Windows·macOS 3.11.9)에서 aelix 프로세스가 로드하는 stdlib 부분. Windows는 여기에 설치본의 콘솔 시험(3.12·3.11 설치본)과 LongPathsEnabled=0의 긴 경로 시험을 더했습니다. 개발 기기 macOS 26.6.2 arm64: CPython 3.12.13(저장소 시험과 end-to-end·스모크·aelix 0.1.0b2 통합), 3.11.15(aelix 0.1.0b2 통합·런처 시험·stdlib 스모크), 3.13.13·3.14.5(저장소 시험, end-to-end 없이). 배포본은 설치 환경 CPython 3.11·3.12에서 `analytics` 런처, aelix 0.1.0b2의 확장 결합·로드·도구, aelix 없이 SDK로 기록한 run과 그 run의 실제 uv 재실행(`run --rerun-of`), 호출자가 둔 `PYTHONPATH` 대신 정확히 설치된 kit이 run에 가는 것을 세 플랫폼에서 실행했습니다(wheel·sdist의 구성원은 빌드한 파일로 확인). 3.11에서만 `python -m labkit`의 stdlib 명령과 분석 라이브러리를 쓰지 않는 스크립트의 run, pip 설치·제거를, 3.12에서만 sdist 설치, README의 symlink 경로(Windows에서는 `os.symlink`로 만든 파일 링크), README의 `uv tool install --python 3.12` 두 줄을 세 플랫폼에서 확인했습니다. `analytics open`은 POSIX에서 stand-in opener로, Windows 포트로는 `os.startfile`을 바꿔 넣어 실행했습니다(실제 opener는 G). 프로젝트 환경은 CPython 3.12 이상(시험이 확인) |
| 프로세스 경계 | 사람용 `analytics` 런처는 설치 환경에서 stdlib만으로 돌고, kit에서는 `_platform`만 로드합니다. stdlib·aelix API·실행 루트의 `aelix_analytics/core.py`와 `_platform`·`relations`로 제한되는 것은 **aelix 확장 프로세스**입니다(설치본은 package의 stdlib 전용 `__init__`과 `_install`도, A). 확장은 `uv run --project` 자식 프로세스로 CLI를 띄웁니다(`extension/aelix_analytics.py`의 `run_kit`). 그 CLI 프로세스는 `runner`·`data`·`report`를 자기 프로세스에서 import하고, `data.py`와 SDK가 그 안에서 duckdb·polars를 import합니다. 실행되는 스크립트도 같은 프로세스입니다 |
| 잠금·타임아웃·종료 | `src/aelix_analytics/_platform.py`의 네 포트 뒤에 있습니다: `FileLocks`(journal·run·report 잠금), `AtomicFiles`(기록 교체), `ProcessControl`(자식 생성·생존 확인·중단 요청·강제 종료), `RunControl`(run 제한 시간과 취소, D1). 구현은 `core.PLATFORM`(모듈을 불러올 때 `_platform.detect()`가 한 번 고른 것)이 기본값이고 `Project(root)`가 그것을 씁니다. 시험과 호출자는 `Project(root, platform=…)`로 다른 구현을 넣습니다. 환경 변수나 기록 속 문자열로 구현을 고르지 않습니다. 호출부에는 `sys.platform` 분기가 없습니다. `ProcessControl`은 문서 열기(`open_document`: macOS `open`, 그 밖의 POSIX `xdg-open`, Windows `os.startfile`)와 프로그램 찾기(`find_program`: POSIX는 `shutil.which`, Windows는 작업 디렉터리와 상대 PATH 항목을 빼고 PATH에서만, 경로로 준 것도 `.exe`만)도 맡습니다. 찾은 프로그램은 절대 경로로, 심볼릭 링크를 따라가지 않고 씁니다(mise의 shim처럼 자기 이름으로 동작을 정하는 링크). POSIX 구현은 `fcntl.flock`, `os.replace`, SIGINT로 묻기, `setitimer`·SIGALRM, `start_new_session`과 `os.killpg(SIGKILL)`입니다. Windows 구현은 `msvcrt.locking`(경합이면 다시 시도), 다른 프로세스가 연 파일의 교체 거절(WinError 5·32·33)을 약 2초 동안 다시 시도하는 `os.replace`, 요청 파일과 `signal.raise_signal(SIGINT)`로 묻기, `threading.Timer`와 `raise_signal`로 제한 시간, `CREATE_NEW_PROCESS_GROUP`과 `taskkill /F /T`입니다. `taskkill`은 시스템 디렉터리(`GetSystemDirectoryW`, 그 호출이 실패하면 `%SystemRoot%\System32`)의 `taskkill.exe`를 절대 경로로 실행합니다 — 이름만 주면 CreateProcess가 호출자의 작업 디렉터리(aelix의 작업 디렉터리, 흔히 프로젝트)를 먼저 찾습니다. 콘솔 이벤트(`CTRL_BREAK_EVENT`)와 `_thread.interrupt_main`은 쓰지 않습니다 — 측정에서 앞의 것은 `uv`를 곧바로 끝내 그 자식을 기록 없이 남겼고, 뒤의 것은 잠든 주 스레드를 깨우지 못했습니다 |
| 생존 확인 | `running` 기록의 pid가 pid일 수 없는 값(0, 음수, bool, 플랫폼 범위 밖)이면 살아 있지 않습니다. 다른 사용자의 프로세스가 그 id를 가지면 재사용으로 보고 살아 있지 않다고 답합니다. 운영체제가 답하지 않으면 살아 있다고 보아 기록을 낮추지 않습니다. 같은 사용자의 프로세스가 재사용한 pid는 구별하지 못합니다 |
| 파일명 — 거절 | Windows 예약 장치명(`CON` `PRN` `AUX` `NUL` `CONIN$` `CONOUT$`, 숫자 1–9와 위 첨자 ¹²³이 붙은 `COM`·`LPT` — 첫 점 앞부분을 끝 공백을 빼고 대소문자 없이 비교)은 **사람이나 에이전트가 새 이름을 정하는 두 지점에서만** 거절합니다: `import --name`(`analytics_import`의 `name`)과 `analytics_run`이 만드는 `scripts/<name>.py`. 이미 있는 이름은 거절하지 않습니다 |
| 파일명 — 스크립트 경로 | run은 어느 플랫폼에서나 같은 뜻으로 읽히는 프로젝트 상대 경로만 기록합니다. 루트(`/`·`\`)나 드라이브(한 글자 뒤 콜론: `C:`, `é:`)로 시작하는 경로, 첫 구성 요소가 `~`로 시작하는 경로, `/`와 `\`로 나눈 구성 요소가 비었거나 `.`·`..`인 경로, 관리 디렉터리(`.venv` `.lab` `runs` `report`) 안의 경로 — `\` 구분자와 대소문자 변형 포함 — 는 기록하기 전에 `script_location`으로 거절합니다. 이전 기록을 재실행할 때도 같은 규칙을 적용하고, 링크를 따라간 실제 위치가 프로젝트 안이며 관리 디렉터리 밖인지 확인합니다. 포함 판정은 철자를 맞춘 실제 경로로 하므로(다른 대소문자로 적은 macOS 절대 경로도 받음) 밖을 가리키는 링크 아래의 스크립트는 프로젝트 밖입니다. 기록은 저장된 철자입니다(kit 0.5.0) |
| 파일명 — run 안 | run 안의 `analytics.save`와 스크립트가 직접 쓰는 경로는 거절하지 않습니다. 보존된 스크립트는 출력 파일이 지워진 뒤에도 다시 실행돼야 하기 때문입니다. 대신 run의 `warnings`에 남깁니다 — 출력과 차트 파일의 **모든 경로 구성 요소**를 검사하므로 `data/derived/con/value.parquet`도 걸립니다. 이 경고는 다른 경고를 가리지 않도록 묶어서 마지막에 둡니다 |
| 파일명 — 차트 | kit이 저장하는 차트 파일(`analytics.visualize`의 이미지·plotly JSON·플롯 데이터)만 이식 가능한 이름으로 바꿉니다(`con` → `con_.png`). 정체성 `script::id`는 생성된 id까지 그대로입니다. 이 대응은 단사입니다 — 차트 id는 slug이라 `_`를 포함할 수 없으므로 `con-file` 같은 보통 id와 충돌하지 않습니다. 스크립트가 차트 id 본래 이름에 쓴 파일(차트 `con`의 `charts/con.png`, 대소문자를 구별하지 않는 파일 시스템에서는 `charts/CON.png`도)은 이전 구현이 그 차트를 쓰거나 덮어쓴 파일이므로 따로 차트로 기록하지도 경고하지도 않고, 그 차트가 버려지면 함께 지웁니다. 그 밖에 스크립트가 run 디렉터리에 직접 쓴 파일(`register_loose_files`)은 스크립트가 정한 이름 그대로 차트가 되며 위 경고로만 알립니다 |
| 경로·인코딩 | 리포트의 절대 경로 링크는 기록한 플랫폼 기준으로 해석한 `file://` URL입니다(`C:\…` → `file:///C:/…`, `\\server\share\…` → `file://server/share/…`). 그래서 POSIX에서 이름이 `X:\`나 `\\`로 시작하는 프로젝트 파일은 그 Windows 경로로 링크되거나, 공유 이름이 없으면 링크되지 않습니다. CLI는 인자를 읽기 전에 표준 스트림을 UTF-8(`errors='replace'`)로 고정하고, 텍스트를 읽는 파일·자식 프로세스 호출은 인코딩을 명시합니다. run 로그도 UTF-8이며 인코딩할 수 없는 문자(짝 없는 surrogate)는 `?`로 씁니다. kit이 쓰는 텍스트 파일(B)은 `core.open_text`·`core.write_text`로만 쓰며(UTF-8, `_platform.TEXT_NEWLINE` = LF, 번역 없음; `tests/test_line_endings.py`의 가드가 확인), run 로그를 감싸는 스트림도 줄 끝을 번역하지 않습니다(kit 0.4.0부터). run 동안 stdout·stderr는 파일 기술자와 Python 스트림을 함께 바꾸고, 출력은 이전처럼 블록 단위로 버퍼링됩니다. 이렇게 해서 콘솔 전용 스트림이 파일에 쓰지 못하는 문제를 피합니다. Windows에서는 설치본 `analytics`를 의사 콘솔(ConPTY)에서 실행해 확인했습니다(측정): run의 stdout 로그에 스크립트의 한국어 출력이 남았고, 콘솔 코드 페이지 437과 949에서 한국어 제목이 콘솔에 그대로 보였습니다. 이것은 Python의 콘솔 경로로 쓴 kit의 출력(결과 줄의 한국어 제목)에 대한 결과입니다. 런처의 글도 같은 경로로 쓰인다고 봅니다(코드에서 읽음, 실행하지 않았습니다). 콘솔 파일 기술자에 바이트를 직접 쓰는 출력과, 출력 파이프가 없는 고전 콘솔의 글 순서·인코딩은 실행하지 않았습니다(`not_run`) |
| 사람용 진입점 | 체크아웃의 `bin/analytics`(POSIX 셸, kit 0.3.0 전 `bin/lab`)과 설치본의 `analytics`(stdlib Python 런처, 모든 플랫폼; Windows에서는 `analytics.exe`)입니다. `bin/analytics`는 wheel로 설치하지 않습니다. 하위 명령은 둘 다 `record`·`render` 등이고 옛 `note`·`report`는 usage와 2입니다. 둘은 같은 명령·인자·usage(바이트까지)·종료 코드와 옛 환경 변수의 오류(D)를 지키고 kit의 출력을 바이트 그대로 넘기며, POSIX에서 같은 stand-in uv로 동등성을 시험합니다. 런처는 자식을 같은 process group·콘솔에서 표준 스트림을 물려준 채 기다리고 SIGINT·SIGBREAK를 기록만 하므로, 터미널의 Ctrl+C를 런처는 다시 보내지 않고 kit의 기록을 기다려 kit의 종료 코드로 끝납니다. uv는 자기 표준 입력이 터미널이 아니면 200 ms 뒤 kit에 한 번 더 보냅니다(`bin/analytics`에서도 같습니다). 런처가 무시된 SIGINT를 물려받으면(셸의 백그라운드 작업) 그대로 두어 kit도 무시한 채 시작합니다. 자식을 시작하는 순간에 온 Ctrl+C는 자식이 뜬 뒤 한 번 더 보냅니다(POSIX). 그 순간 터미널의 Ctrl+C를 이미 받은 자식에게는 두 번 갑니다. Windows에서는 다시 보내지 않습니다 — 한 프로세스에 SIGINT를 보낼 방법이 없고, 시험이 이를 고정합니다. 같은 콘솔의 자식은 콘솔의 Ctrl+C를 직접 받습니다. Windows 콘솔의 Ctrl+C도 런처가 다시 보내지 않고 kit의 기록을 기다려 kit의 종료 코드로 끝납니다(측정: 의사 콘솔과 고전 콘솔에서 런처가 `cancelled` 기록 뒤 3으로 끝났고 남은 프로세스가 없었습니다. Ctrl+Break는 G). 두 번 오더라도 kit은 run을 구간마다 한 번만 중단합니다(D1). 런처와 `bin/analytics`의 차이: 런처만 받은 SIGTERM은 kit의 `run`에만 전하고(run이 `cancelled`로 기록), 그 밖의 단계(init·sync·status·context·import·query·record·render·demo와 `analytics open`의 문서 열기)는 끝까지 두고 다음 단계를 시작하지 않은 채 143으로 끝납니다(`bin/analytics`의 셸은 그 자식을 남기고 곧바로 끝납니다). 이 SIGTERM은 POSIX의 것입니다 — Windows에서는 다른 프로세스가 처리기에 SIGTERM을 보낼 수 없고 `os.kill`·`taskkill /F`가 런처를 곧바로 끝냅니다(강제 종료, D1). 런처가 Ctrl+C나 SIGTERM을 기록한 뒤에는 다음 단계를 — `analytics open`의 문서 열기도 — 시작하지 않습니다. 단계를 시작하는 그 짧은 순간 온 신호는 그 단계를 막지 못할 수 있습니다. 그러면 Ctrl+C는 그 단계에 다시 보내고 SIGTERM은 kit의 `run`에만 보내며, 문서를 여는 프로그램에는 어느 것도 보내지 않으므로 리포트가 열릴 수 있습니다. uv가 없으면 런처는 자기 문구로 127입니다(`bin/analytics`은 셸의 오류로 끝나며, macOS의 sh는 없는 경로에 1). 런처는 macOS에서만 `open`을 씁니다(`bin/analytics`은 `open`이 있으면 어디서나). dash로 도는 `bin/analytics`은 Ctrl+C 뒤 SIGINT로 끝납니다. 측정한 경계 사례의 차이: 빈 첫 인자(`analytics ""`)는 `bin/analytics`에서 status, 런처에서 usage와 2입니다. 실행할 수 없는 uv(디렉터리이거나 실행 권한이 없는 파일인 `AELIX_ANALYTICS_UV`, PATH에서 실행 권한이 없는 `uv` 파일)는 `bin/analytics`에서 셸의 오류와 셸·플랫폼에 따른 종료 코드(126·127·1), 런처에서 uv를 찾지 못했다는 문구와 127입니다. 찾았지만 시작할 수 없는 uv(없는 인터프리터를 가리키는 스크립트)는 `bin/analytics`에서 셸의 오류와 126(macOS의 sh)·127(dash), 런처에서 시작할 수 없다는 문구와 127입니다. 문서를 여는 프로그램이 없으면 둘 다 127이고, 런처는 자기 문구를, `bin/analytics`은 셸의 오류를 냅니다. 작업 디렉터리가 지워졌으면 런처는 읽을 수 없다는 문구와 1로 끝나고, `bin/analytics`은 macOS의 sh에서 셸의 오류와 1로 끝나지만 dash에서는 프로젝트를 찾는 반복이 끝나지 않습니다(kit 0.3.0 전의 `bin/lab`과 같습니다). `AELIX_ANALYTICS_UV`의 `~`와 끝의 `/`는 런처만 풀어 uv를 실행합니다(`bin/analytics`은 dash 127, macOS의 sh 1). 런처 자신의 글을 닫힌 파이프에 쓰면 `bin/analytics`은 141, 런처는 `BrokenPipeError`와 120이고, 표준 출력을 닫은 채(`>&-`) `--help`를 부르면 `bin/analytics`은 2, 런처는 traceback과 1입니다. symlink로 들어온 작업 디렉터리에서 `init ../DIR`은 `bin/analytics`에서 kit이 프로젝트를 만든 뒤 실패하고(셸에 따라 1이나 2), 런처에서는 끝까지 진행합니다. 런처 자신의 글(usage, 안내)은 로캘과 상관없이 UTF-8 바이트입니다. 그 안의 경로·값은 Python의 파일 시스템 인코딩이 글자로 읽은 부분을 그 글자의 UTF-8로, 읽지 못한 바이트를 받은 그대로 씁니다. 그 인코딩이 UTF-8이면(macOS; Linux의 UTF-8 로캘과, Python이 기본으로 UTF-8 모드로 도는 C 로캘) 모든 경로·값이 받은 바이트 그대로이고, 그 인코딩이 Latin-1·EUC-KR 같은 것이면(그런 로캘의 Linux) UTF-8인 이름도 바뀝니다(`bin/analytics`은 늘 받은 바이트). 런처는 `AELIX_ANALYTICS_RUNTIME_PATH`를 따르지 않고 자기 kit을 씁니다 |
| 배포 형태 | wheel 하나를 hatchling이 체크아웃에서 만듭니다(`uv build`): package `aelix_analytics` — kit 전체(체크아웃의 `src/aelix_analytics`: 템플릿·assets·데모 스크립트 포함, 런처 `cli.py`, 실행 루트 위치 `_install.py`, manifest)와 확장 `extension.py`(체크아웃의 `extension/aelix_analytics.py`) — 와, 같은 소스의 사본 `aelix_analytics/kit/aelix_analytics`(실행 루트 `aelix_analytics/kit`). 자식은 실행 루트만 `PYTHONPATH`로 받고 사본에는 `extension.py`가 없습니다. 버전은 0.6.0입니다(`KIT_VERSION`, manifest와 같음). 의존성은 없고, 분석 라이브러리는 프로젝트 환경에만 설치됩니다. aelix 연동은 이 wheel을 aelix의 환경에 설치하는 것이며 extra는 없습니다. 개발 환경은 `package = false`로 프로젝트를 설치하지 않습니다. 라이선스는 Apache-2.0입니다(`LICENSE`; wheel의 `License-Expression`과 manifest의 `license`). 관리자는 GitHub 핸들 `handochan`이고 이메일은 적지 않습니다(wheel의 `Author`·`Maintainer`, manifest의 `authors`). 배포 이름은 첫 공개 전에 정할 사항이고, 이 wheel은 어디에도 게시하지 않았습니다 |

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

- 새 프로젝트 템플릿이 `kit/template/`에서 `kit/labkit/template/`(kit 0.2.0부터 `src/aelix_analytics/template/`)로 옮겨 labkit의 package data가 되었습니다. 그래서
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

kit 0.2.0([#9](https://github.com/handochan/aelix-analytics/issues/9)의 첫 PR, kit을 package `aelix_analytics`로 옮김)에서 다음이
바뀌었습니다. 기록의 형식과 이름(B·C)은 그대로입니다.

- 모듈 이름이 `labkit`에서 `aelix_analytics`로, 체크아웃의 실행 루트가 `<pack>/kit`에서 `<pack>/src`로 바뀌었습니다(D).
  `labkit`을 import하는 스크립트는 안내와 함께 실패합니다(D). `AELIX_LAB_KIT`는 `aelix_analytics/`를 담은 디렉터리를
  가리켜야 하고, 이전 값(`<pack>/kit`)이면 확장이 "kit not found"로 로드되지 않습니다.
- 새 프로젝트의 `lab.json`의 `kit_version`과 run의 `env.kit_version`이 0.2.0입니다. `env.kit_sha256`은 해시하는 package
  디렉터리에 런처 파일(`cli.py`·`_install.py`·`aelix-plugin.toml`)이 더해져 같은 코드에서 한 번 바뀝니다. 체크아웃과 설치본의
  실행 루트가 같은 파일을 담으므로 같은 커밋이면 같은 값이고, 무엇도 이 값을 비교하지 않습니다.
- 설치된 배포본 자신의 package를 `python -m aelix_analytics`로 부르면 거절합니다(D).
- 확장의 telemetry는 kit을 모듈로 부르는 bash 명령에 `python_via_bash`를 붙이지 않습니다. python(또는 `uv run`)에 그 옵션
  다음으로 준 `-m aelix_analytics`와, 문장이 시작하는 자리(명령·줄의 처음, `;` 뒤, `-c` 인자의 여는 따옴표 바로 뒤)의
  `import aelix_analytics`·`from aelix_analytics import`입니다. 이전에는 `labkit`이라는 글자가 든 명령이었습니다. 경로에
  `aelix_analytics`가 들어 있을 뿐이거나 그 글자가 주석·문자열에 있을 뿐인 명령에는 붙습니다. 판정은 정규식이라 문자열 안의
  `; import aelix_analytics`는 구별하지 못합니다.
- 도움말·hint·경고·오류 메시지·리포트의 SDK 호출 예가 `analytics.read(…)` 형태입니다(이전 `lab.read(…)`). 새 run의 기록에
  남는 경고·오류의 글도 그렇습니다. 이전 run의 기록은 바이트 그대로이고, 리포트는 다시 만들 때 새 글을 씁니다. 기록의 `via`
  값(`lab.read`·`lab.chart` 등)은 그대로입니다(B·C, ADR-0005 결정 2).

kit 0.3.0([#9](https://github.com/handochan/aelix-analytics/issues/9)의 둘째 PR, 공개 이름 전환)에서 다음이 바뀌었습니다. 기록의 형식과
저장 이름(B·C의 `lab.json`·`.lab/`·`lab.*/1`·`lab-projects.json`·`via` 값·기록 종류)은 그대로입니다. 위 F1의 항목과 아래 G의 측정은
그때의 이름(`bin/lab`, `/lab`, `lab_*`, `LAB_*`)으로 적었거나 측정한 것이고, 이 PR은 이름만 바꾸어 동작을 바꾸지 않았으므로 G의
현재 서술은 새 이름으로 고쳐 적었습니다.

- 사람용 명령 `bin/lab` → `bin/analytics`(설치본 `analytics`와 usage·메시지가 같은 바이트), 하위 명령 `note`·`report` → `record`·`render`
  (런처·`bin/analytics`·kit CLI. 옛 이름은 사용법 오류). aelix 명령 `/lab` → `/analytics`(`note KIND TEXT`·`gap TEXT` → `record KIND TEXT`·
  `record gap TEXT`, `report` → `render`), 도구 `lab_*` → `analytics_*`(`lab_note` → `analytics_record`, 여섯 개 그대로, 인자 그대로, 응답 첫
  줄의 접두사도 도구 이름), flag `--lab-project` → `--analytics-project`, 확장 파일 `extension/aelix_lab.py` →
  `extension/aelix_analytics.py`(설치본은 그대로 `aelix_analytics/extension.py`), SDK `note`·`chart` → `record`·`visualize`(D).
- 환경 변수 `LAB_PROJECT`·`AELIX_LAB_PROJECT` → `AELIX_ANALYTICS_PROJECT`(하나로 합쳐 CLI와 확장이 함께 읽음, A), `LAB_ACTOR`·
  `LAB_SESSION`·`LAB_STYLE`·`LAB_RUN_ID`·`LAB_RUN_DIR`·`LAB_CANCEL_FILE` → `AELIX_ANALYTICS_*`, `AELIX_LAB_KIT` →
  `AELIX_ANALYTICS_RUNTIME_PATH`, `AELIX_LAB_UV`·`AELIX_LAB_TRUST_FILE` → `AELIX_ANALYTICS_UV`·`AELIX_ANALYTICS_TRUST_FILE`. 사용자가
  설정하던 옛 이름 여덟 개와 옛 flag는 그것을 읽던 쪽이 새 이름을 알리는 오류로 멈춥니다(A, D). 별칭은 없습니다.
- 새 프로젝트의 `lab.json`의 `kit_version`과 run의 `env.kit_version`이 0.3.0입니다. kit이 명령을 가리키는 글(프로젝트가 없을 때의
  hint `analytics init DIR`, run 쪽의 재실행 안내 `analytics run --rerun-of …`, 템플릿 `.gitignore`의 `analytics render`, 데모의
  `analytics demo NEW_DIR`)과 도구 설명·도움말·맥락 블록 머리(`# Active analytics project`)가 새 이름을 씁니다. 새 run의 기록에
  남는 경고·hint의 글도 새 이름입니다(예: 차트 경고의 `analytics.visualize`, `note`·`chart`를 쓴 run의 `error.hint`). 이전 run의
  기록은 바이트 그대로입니다. 새 프로젝트의 `CONTEXT.md` 템플릿도 도구 이름이 바뀌었고, 기존 프로젝트의 `CONTEXT.md`는 그대로입니다.
- 확장의 telemetry `tool` 값은 새 이름이고(C5), 우회 의심 판정은 `bin/analytics`를 부르는 bash 명령에 `python_via_bash`를 붙이지
  않습니다(이전 `bin/lab`). 확장이 core를 로드하는 합성 package의 이름 접두사가 `aelix_analytics_runtime_`입니다(이전 `aelix_lab_kit_`).

kit 0.4.0([#42](https://github.com/handochan/aelix-analytics/issues/42), 줄 끝 — [ADR-0007](decisions/0007-line-endings.md))에서 다음이
바뀌었습니다. 기록의 형식·필드와 저장 이름은 그대로이고, 바뀐 것은 Windows에서 쓰는 바이트와 화면·이벤트의 글입니다.

- 모든 플랫폼: 새 프로젝트에 `.gitattributes`(`* -text`, B)가 생기고 run의 출력 비교에서 빠집니다. 줄 끝만 바뀐 입력·코드에 사유
  `line_endings_only`가 붙고(E; JSON에 입력의 `reason`, status 차트 행의 `change_reasons`·`code_reason`가 더해짐), 줄 끝만 바뀐 CONTEXT
  버전의 이벤트 글이 `CONTEXT.md line endings changed`입니다(C5). inline 스크립트와 kit이 쓰는 HTML(Workspace 페이지, `html`·altair 차트)은
  내용의 CR까지 LF로 씁니다. 템플릿에 채우는 제목·목표의 CR도 LF가 됩니다. 해시 캐시 항목에 줄 끝 변형의 해시가 더해질 수
  있습니다(`.lab/cache/`, 파생물; 옛 kit은 그 항목을 다시 계산합니다). altair 차트는 경로 대신 버퍼로 받아 씁니다.
- Windows: 기록(JSON·JSONL)·템플릿 사본·`.python-version`·inline 스크립트·Workspace·차트 HTML·JSON·build stamp·데모의 `CONTEXT.md`·
  run 로그(스크립트의 `print`)가 CRLF에서 LF가 됩니다. 그래서 같은 내용의 기록과 inline 스크립트의 `script.sha256`이 다른 플랫폼과
  같습니다. 저장소가 LF이므로(루트 `.gitattributes`) 새 체크아웃과 그 wheel, `env.kit_sha256`도 다른 플랫폼과 같습니다. 스크립트가
  남긴 스레드가 CLI run 뒤에 출력한 글도 LF입니다(결과 JSON 줄은 그대로 CRLF, G).
- 새 프로젝트의 `lab.json`의 `kit_version`과 run의 `env.kit_version`이 0.4.0입니다. kit 0.4.0 전에 Windows에서 쓴 기록은 CRLF 그대로
  읽고 고쳐 쓰지 않습니다(G).

[#32](https://github.com/handochan/aelix-analytics/issues/32) 1번(결과 줄의 Unicode 줄 구분 문자)에서 다음이 바뀌었습니다. 기록과
`kit_version`은 그대로이고, 바뀐 것은 CLI 결과 줄의 바이트와 그 줄을 읽는 방식입니다.

- CLI의 결과 줄이 U+2028·U+2029·U+0085를 `\u2028`·`\u2029`·`\u0085`로 이스케이프해 씁니다(D). 이전에는
  `json.dumps(ensure_ascii=False)`대로 그대로 썼고, 확장이 kit의 stdout을 `str.splitlines()`로 나누어 그 줄이 끊겼습니다. 그래서
  run의 stdout·stderr·오류·제목, `query`의 값, `import`한 데이터셋의 설명처럼 결과에 그 문자가 들어간 명령은, 명령은
  끝났는데도(run은 기록됐는데도) 확장에 `kit_failed … without a result`로 보였습니다(이전 구현 `48b79cc`에도 있던 결함). CLI
  `record`의 결과 줄에도 note의 글이 들어가 이제 이스케이프됩니다. 확장의 `analytics_record`·`analytics_status`는 kit을 프로세스
  안에서 불러 이 줄을 읽지 않고, 스크립트의 `analytics.record` 글은 run의 결과에 들어가지 않습니다.
- 확장은 kit의 stdout을 LF에서만 나누고 LF 앞의 CR은 버립니다. 이전 kit이 그 문자를 그대로 쓴 결과 줄도 온전히 읽습니다. 앞뒤
  공백을 지운 뒤에도 CR이 남은 줄은 결과로 받지 않으므로, 단독 CR을 담은 스레드의 글을 결과로 받지 않는 것은 이전과 같습니다.
- kit의 데모, 저장소 도구(`tools/platform_smoke.py`)와 시험 helper는 `str.splitlines()` 대신 LF에서 나눕니다. 텍스트 모드로
  읽으므로 CRLF와 단독 CR은 그 전에 LF로 바뀌고, 세 문자는 줄을 나누지 않습니다.

[#10](https://github.com/handochan/aelix-analytics/issues/10)(영어 기본 UI와 사용자 글의 보존)에서 다음이 바뀌었습니다. 기록의 형식·필드,
`kit_version`, 저장 이름은 그대로이고, 바뀐 것은 kit이 보이는 글과 새 프로젝트·데모의 기본값입니다.

- Workspace: 탭·제목·배지·버튼·빈 화면·tooltip·aria-label·오류 글이 영어이고 문서의 `lang`이 `en`입니다(이전 `ko`). 탭 이름은
  Overview·Visualizations·Timeline·Data·Runs·Context·Evaluation, 평가 탭의 구역은 Workflow feedback·Record coverage·Tool usage·Possible
  workarounds입니다. 기록 종류의 label은 `todo`가 Next step, `gap`이 Workflow feedback이고(기록의 값은 그대로), run 상태는 Succeeded·Error
  등, 재실행 비교는 Matched·Different·Run failed·Inconclusive(기록의 `verdict`는 그대로)입니다. 차트의 첨부 표는 `Attached data`, 같은
  스크립트의 최신 run에 없는 차트는 `Absent from latest run`입니다. 용량 단위가 `KiB`·`MiB`·`GiB`입니다(계산은 이전과 같은 1024 단위).
  URL anchor·요소 id·class·링크 대상은 그대로입니다. CSS 글꼴 목록에 Windows·Linux의 한국어 글꼴이 더해졌습니다.
- 사용자 내용(Workspace): 사람과 스크립트가 쓴 글은 번역하거나 Unicode 정규화하지 않고 쓴 그대로 보입니다. `CONTEXT.md`는
  Markdown으로 그리고(HTML은 그리지 않아 글자로 보임), 그 밖의 글(기록 글, 제목, caption, 열 이름과 값, 데이터셋 이름, 경로)은 HTML
  escape만 거칩니다(링크 대상은 퍼센트 인코딩, 페이지의 줄 끝은 LF, 표 미리보기는 긴 값을 줄임). 그 글을 담은 요소는 `lang=""`(언어
  모름)이고 kit의 label은 문서의 `en`을 따릅니다. 언어를 추정하지 않고, 화면 낭독기는 그 글을 읽는 사람의 기본 음성으로 읽습니다
  (문서 제목 `<title>`은 kit의 글과 이어 쓴 한 문자열이라 `en`). 데이터셋은 쓴 그대로의 이름(파일 stem)으로 보입니다(이전에는 SQL
  view 이름): 데이터 탭, 데이터셋 페이지의 제목, 입력·출력·참조의 링크 글. 이름과 view가 다르면(예: `My Sales`→`my_sales`,
  `123sales`→`t_123sales`, `data/derived/지역별 매출.parquet`→`___…`, 겹치는 derived의 `…_derived`) 데이터 탭과 데이터셋 페이지가
  view를 옆에 적습니다. view 이름 규칙(E)과 데이터셋 페이지의 파일 이름(`report/data/<view>.html`)은 그대로입니다.
- 새 프로젝트: `CONTEXT.md` 템플릿이 영어이고(섹션 Goal·Analysis focus·Next steps·Current findings·Definitions and rules·Data sources·
  Assumptions and limitations, 고정 표식 `<!-- pin -->`), 목표 없이 만들면 넣는 안내 줄도 영어입니다. uv 프로젝트 이름이
  `analytics-<slug>`입니다(이전 `lab-<slug>`; 새 프로젝트의 `pyproject.toml`과 uv가 만드는 `uv.lock`에만 들어가고 kit은 읽지 않음).
  기존 프로젝트의 파일은 바꾸지 않습니다.
- 맥락의 읽기: `<!-- pin -->`도 섹션을 고정합니다(B). 이름이 표식뿐인 섹션의 이름이 `Section N`입니다(이전 `섹션 N`; status JSON의
  `context_omitted`와 맥락 블록). `analytics_status`·kit `status`의 안내가 `<!-- pin -->`을 권합니다(이전 `<!-- 항상 -->`). #10 전의 kit은
  `<!-- pin -->`을 모르므로 새 템플릿의 섹션을 고정하지 않고 이름에 표식을 남깁니다(내용을 잃지는 않음: 예산 안이면 들어가고, 아니면
  줄 범위로 목록에 남음).
- 데모(`analytics demo`): CONTEXT·데이터셋 설명·run 제목·기록 글·스크립트(제목·축·범례·caption)가 영어이고, 합성 데이터의 지역 값이
  Seoul·Gyeonggi·Busan·Daegu입니다(이전 한국어 지역명; 그 밖의 값은 같은 시드로 같음). 자동 캡처한 차트의 id는 제목에서 만들므로
  데모 차트의 id도 달라집니다. 데모의 CONTEXT는 템플릿의 섹션 순서와 고정을 따릅니다.
- 확장의 도구 설명·응답, CLI·런처의 도움말·오류는 이미 영어였고 바뀌지 않았습니다.

kit 0.5.0([#36](https://github.com/handochan/aelix-analytics/issues/36), 경로 정체성, ADR-0006 9절)에서 다음이 바뀌었습니다(모든 플랫폼).
기록의 형식·번호(`lab.run/1`·`lab.dataset/1`)·저장 이름은 그대로이고, 기록이 담는 경로 값의 규칙이 바뀌었습니다(E의 경로 정체성).
측정은 [측정 기록](verification/2026-10-06-issue-36-path-identity.md)에 있습니다.

- 밖을 가리키는 디렉터리 링크(심볼릭 링크, junction) 아래를 대상마다 한 번 조회해, 그 아래의 출력·데이터셋·입력을 프로젝트 상대
  경로로 기록합니다. `data/`가 심볼릭 링크인 프로젝트에서 run의 출력이 처음으로 기록되고(이전에는 `outputs: []`), 입력은 `data/…`·
  `external: false`이며 데이터셋의 producer·`used_by`가 이어집니다. Windows의 junction인 `data/`는 출력은 이전처럼, 입력·데이터셋
  경로가 프로젝트 상대 경로로 바뀝니다. 밖을 가리키는 파일 링크는 링크의 경로입니다. 같은 대상에 경계 여럿으로 닿으면 코드 포인트
  순으로 가장 앞인 경계의 경로입니다.
- 안을 가리키는 링크와 루트·조상·관리 디렉터리(`runs`·`.venv`·`.lab`·`report`)로 풀리는 링크로는 내려가지 않습니다. 그래서
  `runs/`를 가리키는 Windows의 junction이 run 자신의 `stdout.txt`·`stderr.txt`를 출력으로 기록하던 것(경고 셋)이 없어졌고, 관리
  디렉터리가 안을 가리키는 링크(`runs → storage/runs`)이면 그 실제 위치 아래도 출력 비교에서 빠집니다(이전에는 출력과 `outside_data`
  경고로 기록했습니다). 관리 디렉터리가 링크이거나 다른 대소문자로 저장돼도(`Runs`) 그 아래의 정체성은 kit의 이름 아래 경로라,
  입력 제외(`runs/<id>/`·`.venv/`·`.lab/`)·차트 정리·모듈 스냅샷의 제외가 실제 위치와 철자에 관계없이 듭니다(이전에도 기록은
  입력한 철자인 `runs/…`였습니다). 사람의 디렉터리로 가는 안쪽 링크(`scripts → src`)는 이전처럼 실제 위치(`src/…`)이고 그 아래는
  출력 비교 대상입니다. 시작 위치를 준 조회는 이전의 `rglob`처럼 안을 가리키는 파일 링크(`latest.parquet → v3.parquet`)를
  데이터셋으로 내며, 그 경로는 대상의 정체성입니다. 시작 위치가 루트나 그 조상으로 풀리면(`data/raw → /`) 아무것도 걷지 않습니다
  (이전 `rglob`·`os.walk`는 파일 시스템 전체를 걸었습니다). 디렉터리 읽기의 500개는 경로의 구성 요소별 코드 포인트 순(보통 디렉터리의
  트리에서)으로 처음 것이고(POSIX에서 이전과 같음; Windows의 이전 `rglob` 순서는 대소문자를 무시했습니다), 조회가 500번째에서
  멈춥니다(이전에는 트리 전체를 모은 뒤 골랐습니다). 최상위의 제외 이름은 파일이어도(워크트리의 `.git` 파일) 출력 비교에서
  빠집니다(이전 `os.walk`는 디렉터리만 이름으로 뺐습니다).
- Windows: `data/raw`·`data/derived` 아래에서 안을 가리키는 junction으로 데이터셋 목록이 내려가지 않습니다(이전 `rglob`은 junction
  아래로 내려가 그 `.parquet`을 데이터셋으로 냈습니다; POSIX의 심볼릭 링크는 이전에도 내려가지 않았습니다). 그 파일이
  `data/raw`·`data/derived`의 다른 곳에 있으면 실제 위치의 경로로 목록에 있고, 그 밖이면 목록에 없습니다.
- 기록하는 모든 경로와 프로젝트 루트(`status`의 `root`, 맥락 블록)·신뢰 목록 항목의 철자가 저장된 이름입니다(macOS의 대소문자·
  NFD; 관리 디렉터리의 이름은 위의 예외). 다른 대소문자로 적은 스크립트 경로는 저장된 경로로 기록되어 차트 정체성이 하나이고, 디렉터리를 다른 대소문자로 적은 절대
  경로의 스크립트를 받으며, 다른 대소문자로 읽은 입력이 producer와 이어지고, 상위 디렉터리를 다른 대소문자로 적어 만든 프로젝트가
  신뢰됩니다. 옛 철자로 적힌 신뢰 목록 항목도 철자를 맞춰 비교합니다. 데이터셋 원본의 `source.path`도 저장된 철자입니다.
- 신뢰 목록이 대상이 있는 심볼릭 링크면 그 대상을 같은 디렉터리에서 원자적으로 교체합니다(이전에는 링크를 보통 파일로 바꿨습니다).
- view 이름은 kit 이름 규칙에 맞는 stem이 먼저 받고, `status`가 대소문자만 다른 데이터셋 이름을 경고합니다(E). 데이터셋 목록은
  `.parquet` 접미사를 대소문자 없이 봅니다(POSIX에서 `X.PARQUET`이 처음 보입니다; Windows의 `rglob`은 원래 그랬습니다). 목록의
  순서는 정체성의 구성 요소별 코드 포인트 순입니다(Windows의 이전 순서는 대소문자를 무시했습니다).
- `analytics_query`가 `Permission Error`로 거절하면 hint가 실제 위치로 판정한다는 것을 덧붙입니다. 판정은 그대로입니다.
- telemetry의 `wrote_managed_path`가 링크를 거친 쓰기(밖을 가리키는 `data/` 아래 등)와 다른 대소문자로 저장된 관리 디렉터리
  (`Runs/…`) 아래의 쓰기에도 붙습니다. 판정은 경로의 글자(루트 아래가 아니면 링크를 푼 실제 경로)로 하고 디렉터리 목록을 읽지
  않습니다(aelix의 이벤트 루프에서 도구 호출마다 돕니다).
- `env.kit_version`과 새 `lab.json`의 `kit_version`이 0.5.0입니다. 경계 이름 `core.PATH_IDENTITY_SINCE = "0.5.0"`은 고정이며, 그보다
  낮은 `env.kit_version`(PEP 440)의 run이 옛 규칙의 경로입니다. kit은 옛 기록을 고쳐 쓰지 않고, 옛 경로를 지금의 이름 공간에
  대응시키는 함수(`Project.legacy_identity`)를 [#12](https://github.com/handochan/aelix-analytics/issues/12)의 읽기 모델에 넘깁니다. #12의 셋째
  PR부터 읽기 모델이 그 함수로 옛 run의 경로를 추론으로 읽습니다(E "옛 kit의 경로"): 링크한 `data/`의 옛 절대 경로 입력은 새 데이터셋
  경로와, macOS에서 다른 철자로 기록된 옛 스크립트 경로의 차트 버전은 새 run의 것과 이어지고, 그 연결마다 추론이라고 표시합니다(그 전에는
  이어지지 않고 나뉘었습니다). 옛 run을 재실행하면 입력 경로 문자열이 달라
  `reproduction.inputs_same`이 거짓일 수 있습니다(판정 `verdict`는 출력·차트 비교라 그대로이고, 옛 기록에 없던 출력이 새로 기록되는
  것은 비교하지 않습니다). 해시 캐시는 철자를 맞춘 경로를 키로 하므로, 다른 철자로 열던 프로젝트에서 한 번 다시 해시합니다(run의
  입력은 판정이 닿은 실제 경로로, `status`는 정체성의 경로로 열므로 밖을 가리키는 링크 아래의 파일은 키가 둘입니다).
- 준비 구간에서 트리 조회를 정적 스캔·선언 입력보다 먼저 합니다(같은 취소 구간, D1 그대로). run이 링크를 만들거나 지우거나 다른
  대상으로 바꾸면 기록 단계에서 입력·출력 비교·`lab.save` 표시를 지금의 이름 공간으로 다시 정해, 바뀌지 않은 파일이 지워지고 새로
  생긴 것으로 보이지 않습니다. 그런 링크 아래의 파일은 G의 규칙으로만 출력입니다: 있던 디렉터리를 링크하고 한 파일을 읽은 run은 그
  파일이 입력이고 출력이 없습니다(이전에도 출력은 없고 입력은 링크를 푼 절대 경로였습니다). 그 run이 새 링크를 거쳐 쓴 파일은
  출력입니다(이전에는 링크 아래를 보지 않아 기록하지 않았습니다).

kit 0.6.0([#13](https://github.com/handochan/aelix-analytics/issues/13)의 첫 PR ⓪, 내부 이름 — [설계](design/RECORD-WRITER-AND-MIGRATION.md#9-미뤄-받은-이름과-pr)
9절)에서 다음이 바뀌었습니다(모든 플랫폼). 기록의 형식·번호·저장 이름(B·C)과 기록하는 값은 그대로이고, 아래의 오류 클래스 이름만 예외입니다.

- kit CLI 결과 JSON의 키 `report` → `workspace`(`import`·`record`·`render`·`run`·`demo`의 Workspace 빌드 결과와 `status`의 `index.html` 경로),
  `run`의 `report_error` → `workspace_error`(D). 값은 그대로이며 경로는 `report/…`입니다. 확장은 새 키를 읽습니다. 배포본을 게시하지
  않았으므로 옛 키를 읽는 외부 소비자는 없습니다(ADR-0005 결정 8).
- Workspace의 자원 `assets/report.js`·`assets/report.css` → `assets/workspace.js`·`assets/workspace.css`(내용은 아래 저장 키와 전역 이름만
  다름). 렌더가 페이지를 쓴 뒤 옛 두 파일을 지웁니다. `assets/build.js`는 `window.__ANALYTICS_BUILD__`를 정합니다(그 전은
  `window.__LAB_BUILD__`). 그래서 0.6.0 전 kit이 만든 페이지를 연 채로 둔 탭은 0.6.0의 첫 렌더 뒤 자동으로 다시 읽지 않습니다. 한 번 직접
  새로 고치면 됩니다.
- Workspace의 브라우저 저장 키 `lab-live`(localStorage, 자동 새로고침 설정) → `analytics-live`, `lab-scroll:<경로>`(sessionStorage, 다시
  읽기 전의 스크롤 위치) → `analytics-scroll:<경로>`. 새 키가 없으면 옛 키의 값을 한 번 읽어 새 키에 쓰고, 옛 키는 지우지 않습니다(0.6.0 전
  kit의 Workspace가 읽습니다). 스크롤 위치는 쓴 뒤 새 키를 비워 두어(지우지 않아) 옛 키를 다시 읽지 않습니다.
- 공개 계약이 아닌 내부 이름: `core.LabError` → `core.AnalyticsError`(JSON 오류의 형태 `{"ok": false, "error": {code, message, hint?,
  details?}}`와 코드는 그대로), `Project.lab`·`Project.report` → `Project.state_dir`·`Project.workspace_dir`(값 `.lab`·`report` 그대로), 모듈
  `report.py` → `workspace.py`, 확장의 `schedule_report` → `schedule_workspace_render`, plotly 기본 템플릿 `plotly_white+lab` →
  `plotly_white+analytics`(저장하는 그림 JSON에는 템플릿의 내용만 있고 이름이 없어 바이트가 같습니다), capture의 표식 `__lab_wrapped__` →
  `__analytics_wrapped__`. 오류 클래스의 이름을 글로 담는 곳에서는 새 이름이 보입니다: run 기록의 `finalize_errors`(예: run 안에서
  빈 글로 `analytics.record`를 부르면 `note finding: AnalyticsError: note text is empty`, 그 전은 `LabError`)와 Workspace 빌드 실패의
  글(`{error}`·`workspace_error`).
- 새 프로젝트의 `lab.json`의 `kit_version`과 run의 `env.kit_version`이 0.6.0입니다.

## G. 약속하지 않는 것

- 격리·샌드박스·승인. `analytics_run` 스크립트는 사용자가 읽을 수 있는 모든 파일에 닿습니다(`analytics_query`만 `data/`로 묶여 있습니다).
- 계보의 완전성. 입력 감지는 최선 노력입니다(pyarrow 직접 읽기처럼 훅이 없는 경로는 놓칩니다). 일부만 감지된
  경우 경고도 없습니다 — 경고는 입력이 하나도 없을 때만 나옵니다. 행 단위 계보와 선택(마킹) 기반 후속 분석은 없습니다.
- 관계의 읽기(#12).
  - 앞 참조. `/1` writer는 `refs.events`의 대상이 있는지 확인하지 않아, 아직 없는 `e<seq>`나 오타 id를 적을 수 있습니다. reader는 대상 seq ≥ 참조한 기록의 seq인 이벤트 계열 참조를 `missing`(`record_absent`)·`confirmed=false`로 읽습니다(E). ADR-0006 4절 2단계의 읽기이고 ADR 본문은 고치지 않았습니다. 그래서 그런 참조가 나중에 생긴 다른 기록과 확정 연결이 되지는 않지만, 쓰기는 여전히 받습니다(#13 writer).
  - 핵심 정보·열린 항목의 선택은 C1의 규칙(`refs.resolves`가 가리키는 id) 그대로이며 ADR-0006 6절의 상태 재생과 다를 수 있습니다. 여러 줄의 id를 닫는 `resolves`는 재생에서는 아무것도 닫지 않지만(`ambiguous`), 목록에서는 그 id의 줄을 모두 닫힌 것으로 셉니다. `add_note`의 `resolves` 확인은 journal만 읽고 그 id의 첫 줄로 통과합니다. 상태 재생을 화면에 쓰는 일은 #16입니다. 여러 줄의 id는 #49 수정 전의 kit이 끊긴 줄 뒤에 쓴 journal이나 손으로 고친 journal에 있습니다. 지금 writer는 이미 읽히는 줄의 seq나 id를 다시 주지 않습니다(C1).
  - `lab.json` 프로젝트의 `analytics.event/2` 줄은 읽어 그 참조를 판정하지만 목록(핵심 정보·열린 항목·최근 기록·타임라인)에 보이지 않고, 개수와 위치만 적습니다(`not listed:`). 표시는 #13의 첫 PR입니다.
  - 다른 OS가 기록한 절대 경로는 이 기계에서 파일이 없어 `missing`/`file_absent`로, 그 입력의 생산자는 이 기계의 기록과 경로가 맞지 않아 `none`으로 보입니다(Freshness의 전과 같습니다). 옛 kit 경로의 추론(E)은 이 기계의 지금 루트·링크 대상 아래 경로와 파일 시스템이 같은 항목으로 여는 철자만 대응시킵니다. 그래서 다른 OS·옮긴 프로젝트의 절대 경로, 대소문자·정규화를 구별하는 파일 시스템에서 다른 철자로 기록된 상대 경로는 대응하지 않습니다(글자를 접어 맞추지 않습니다).
  - 옛 kit 경로의 추론은 오늘의 링크·이름으로 판정합니다. 그 run 뒤에 링크를 바꿨거나 파일을 옮겼으면 다른 파일로 대응할 수 있어 확정 연결로 세지 않습니다. 표시는 그 판정 자리마다입니다: stale을 따라가는 길의 중간(후보의 후보)에서 쓴 추론은 그 차트에 표시하지 않고, 직접 입력의 후보만 표시합니다. 맥락 블록(journal만 읽음)은 경로를 읽지 않습니다. kit 버전이 없는 이벤트의 `refs.charts` 글자는 어떤 옛 run이 그 키를 기록했을 때만 그 대응을 따르고, `refs.data`는 대응하지 않습니다. 대응이 지금 있는 항목인지는 읽는 프로세스가 열 수 있는 경로로 봅니다(Windows에서 LongPathsEnabled가 0이면 260자를 넘는 경로는 없는 것으로 보아 대응하지 않을 수 있습니다).
  - 비용(측정은 이 PR의 기록). 맥락 블록은 프롬프트마다 journal만 읽어 줄마다 기록을 판정하므로 줄 수에 비례하고, `analytics_status`는 run.json을 한 번, 기준 파일들을 두 번 stat합니다. Workspace는 타임라인의 모든 참조를 판정합니다. 이벤트를 쓸 때도 잠금 안에서 journal 전체를 읽어 seq를 정하므로 쓰기 한 번이 줄 수에 비례합니다(#49의 PR이 macOS arm64에서 잰 값: 10,000줄·2.6 MB journal에 쓰기 한 번 약 30 ms. 그 전에는 끝 64 KiB만 읽어 0.2 ms 미만). 영속 인덱스는 없습니다(#15).
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
  `incomplete`로 보이고, 그런 `AELIX_ANALYTICS_SESSION`은 CONTEXT.md가 바뀐 뒤의 맥락 동기화에서 JSON 줄 없이 traceback으로
  끝납니다(이전 구현과 같습니다). 정적 입력 스캔은 짐작일 뿐이므로 그런 이름을 입력으로 넣지 않습니다(F1). 그런 이름을
  기록에 어떻게 담을지(이스케이프, 치환, 거절)는 정하지 않았습니다. Windows(NTFS)도 짝 없는 surrogate가 든 이름을 짝 없는
  UTF-16 단위로 담고 같은 str로 돌려주므로 같은 경우가 생깁니다: 정적 입력 스캔이 그런 이름을 빼고 run을 `ok`로 기록하는
  것을 Windows에서도 확인했습니다(측정). run이 그런 이름의 파일을 읽거나 쓰는 경우는 Windows에서 실행하지 않았습니다.
- 경로 정체성의 한계(E, ADR-0006 9절, [측정 기록](verification/2026-10-06-issue-36-path-identity.md)).
  - 링크 대상의 절대 경로로 직접 읽었거나 링크 뒤의 `..`로 경계를 벗어나 읽은 파일은 프로젝트 밖 경로입니다. 링크 뒤의 `..`를
    글자로 접지 않는 것은 POSIX의 의미이고, Windows는 Win32가 여는 대로 글자로 접은 경로로 판정합니다(시험이 둘을 나눠 고정).
  - 같은 대상에 닿는 경계가 여럿이면 코드 포인트 순으로 가장 앞인 경계의 경로입니다. 그래서 경로가 더 앞인 다른 링크(`aaa/`)가
    `data/raw/`와 같은 디렉터리를 가리키면 그 파일은 `aaa/…`로 기록되고 `outside_data` 경고를 받습니다. 데이터셋 목록에는
    `data/raw`에서 찾은 것으로 남고 경로는 `aaa/…`입니다.
  - `analytics_query`는 실제 위치로 판정하므로 `data/` 안에서 밖을 가리키는 링크 아래의 데이터셋은 view에 있지만 질의가
    거절됩니다(hint가 이유를 말하고, run의 `analytics.read`는 읽습니다). `data/` 자체가 링크인 것은 읽습니다.
  - 밖을 가리키는 링크 아래도 run마다 조회하므로 큰 디렉터리(그 안의 중첩 링크까지)를 링크하면 그만큼 느려지고, run 동안 다른
    프로세스가 그 아래에서 바꾼 파일도 출력으로 기록됩니다(프로젝트 안 디렉터리와 같음). 경계 아래의 경로를 처음 정하는 명령
    (`status`, import 등)도 링크를 찾는 조회를 한 번 합니다.
  - 조회가 걷지 않는 경계(최상위 제외 디렉터리 안, 조회 뒤 run 중에 생긴 링크)는 그 경로가 건넌 경계로만 정하므로, 다른 경계가
    같은 대상에 닿아도 그 경계의 경로가 될 수 있습니다.
  - run 중의 링크 변경. 실행 전 조회와 기록 단계 조회의 경계(이름 공간 경로, 대상)가 다르면 run이 링크를 만들거나 지우거나 다른
    대상으로 바꾼 것입니다. 새 경계 아래의 파일은 스크립트가 시작한 시각 — run의 stdout 로그를 열 때 파일 시스템이 찍은 수정
    시각 — 이후에 수정됐을 때만 출력(`created`·`modified`)이고 그 밖의 파일은 비교하지도 해시하지도 않습니다. 사라진 경계 아래에
    있던 파일은 실제 파일이 없어졌을 때만 `deleted`입니다. 그래서 그 대상이 프로젝트와 다른 시계(네트워크·클라우드 볼륨)나 더 거친
    시각(FAT 2초, HFS+ 1초)을 쓰면 run이 쓴 파일을 놓치거나 run 직전에 바뀐 파일을 출력으로 적을 수 있고, run 동안 다른 프로세스가
    새 경계 아래에서 바꾼 파일도 출력입니다. 링크를 거친 판정은 기억하지 않지만 보통 디렉터리까지의 판정과 디렉터리 목록은 스크립트
    동안 기억하므로, run이 그 아래를 읽은 뒤 그 디렉터리를 링크로 바꾸고 다시 읽으면 두 읽기가 처음 읽기의 경로 하나로 합쳐져 나중
    바이트만 남습니다(연 파일은 그때의 것).
  - 기록 교체는 링크 자체를 바꿉니다(의도한 규칙, 세 OS의 시험이 고정). 신뢰 목록만 대상이 있는 링크면 그 대상을 교체합니다.
    append 쓰기(journal·telemetry·queries)는 링크를 따라갑니다(시험). 막아야 하면 새 Issue로 다룹니다.
  - 디렉터리 목록을 읽을 수 없으면 입력한 철자를 둡니다. run 중에 대소문자만 바꿔 다시 만든 파일은 옛 철자로 남을 수 있습니다.
    목록을 다시 읽을지는 디렉터리의 수정 시각으로 정하므로(바뀐 지 2초가 안 되면 시각을 믿지 않음, E), 파일 시스템의 시계가
    kit의 시계와 2초 넘게 다르면(네트워크·클라우드 볼륨, 실행하지 않음) 그사이 대소문자만 바꾼 이름이 옛 철자로 남을 수 있습니다.
    한 디렉터리에서 목록에 없는 새 이름을 많이 읽는 run은 그 목록을 처음 여덟 번 뒤로는 점점 드물게 다시 읽으므로(파일마다 다시
    읽으면 파일 수의 제곱만큼 느려집니다), 그사이 만든 철자와 다른 철자로 읽은 새 파일은 읽은 철자로 남을 수 있습니다. 하드 링크는 이름마다 다른 경로입니다(이전과 같음). bind mount처럼 링크가 아닌 별칭은 루트에 닿는 경우와, 조회가 걸은
    디렉터리와 파일 키가 같은 경우에만 같은 곳으로 봅니다.
  - 대소문자·정규화를 무시하는지는 플랫폼이 아니라 실행 중 같은 디렉터리 항목인지로 정합니다. NTFS·ext4는 NFC와 NFD를 다른
    이름으로 둡니다(그 OS에서는 별칭이 없습니다, 2026-10-02 조사 실행 36899933022에서 측정).
  - kit 0.5.0 전의 기록은 고쳐 쓰지 않습니다(F1). 옛 기록의 절대 경로와 **상대** 경로(macOS에서 저장되지 않은 철자로 기록된
    스크립트·입력 경로)는 읽기 모델이 같은 함수로 철자를 맞춰 추론 대응(`basis: inferred`)합니다(E "옛 kit의 경로",
    [2026-10-06 소유자 결정](https://github.com/handochan/aelix-analytics/issues/12#issuecomment-6009076270)).
  - 대소문자를 구별하는 APFS 볼륨(디스크 이미지)은 개발 기기에서 실행했습니다(측정 기록). 실행하지 않은 것: Linux의 casefold
    디렉터리(ext4 `+F`), HFS+, 네트워크·클라우드 볼륨, `st_ino`가 0인 파일 시스템(경로로 판정하는 대비책은 단위 시험만), bind mount
    별칭.
- 줄 끝([ADR-0007](decisions/0007-line-endings.md), kit 0.4.0부터, [#42](https://github.com/handochan/aelix-analytics/issues/42)). 저장소는 LF이고(루트
  `.gitattributes`의 `* text=auto eol=lf`와 바이너리 형식의 `binary`; 기록 예제 `tests/fixtures/records/`는 `* -text`), kit이 쓰는 텍스트(B)는
  모든 플랫폼에서 UTF-8·LF입니다([측정 기록](verification/2026-10-06-issue-42-line-endings.md)). 남은 한계:
  - kit 0.4.0 전에 Windows에서 쓴 기록·템플릿 사본·inline 스크립트·Workspace·run 로그는 CRLF로 남고 그대로 읽습니다. 고쳐 쓰지 않으며,
    새 이벤트를 LF로 이어 써 journal에 두 줄 끝이 섞일 수 있습니다(그런 journal의 읽기는 `mixed-version` 예제가 시험합니다). 옛 kit이
    같은 프로젝트에 다시 쓰면 CRLF 줄이 다시 섞일 수 있습니다(`lab.json`(`/1`)에는 쓰는 kit의 하한 표식이 없습니다).
  - 사용자 파일, 스크립트가 버퍼·파일 기술자로 쓴 바이트, 자식 프로세스의 출력, aelix의 write·edit 도구가 쓴 `CONTEXT.md`·스크립트는
    바이트 그대로라 CRLF일 수 있습니다. 그래서 Windows의 run 로그에는 스크립트의 `print`(LF)와 자식 프로세스의 CRLF가 섞일 수
    있습니다. Workspace에 복사하는 서드파티 `plotly.min.js`도 바이트 그대로입니다(지금 번들된 파일에는 CR이 없습니다).
  - `line_endings_only`(E)는 CRLF와 LF의 차이만 알아봅니다. 기록할 때 줄 끝이 섞여 있던 파일, 단독 CR, 64 MiB를 넘거나 앞 8 KiB에
    NUL이 있는 파일, 읽는 동안 바뀐 파일은 사유 없이 `changed`입니다. 상류 후보의 stale, 참조의 `content_changed`, 출력의 현재 상태,
    재실행 비교(`inputs_same`, 출력·차트의 바이트)는 바이트로만 판정합니다.
  - CLI의 표준 출력(결과 JSON 줄과 안내)은 파일이 아니라 스트림이어서 플랫폼의 줄 끝(Windows CRLF)입니다. CLI run 뒤 스크립트가
    남긴 스레드가 출력하는 글은 LF라 한 스트림에 두 줄 끝이 섞일 수 있습니다(확장은 LF에서 나누고 그 앞의 CR을 버리며, 런처는
    바이트를 그대로 넘깁니다).
  - 기존 프로젝트에는 `.gitattributes`를 만들지 않습니다(#13의 전환 보고서가 제안). git의 줄 끝 변환을 거쳐 플랫폼을 오간 그
    프로젝트의 텍스트 입력과 스크립트는 `changed (line endings only)`로 보입니다. 새 프로젝트의 `.gitattributes`는 다른 git 저장소 안에
    만들면 그 하위 트리에 한해 상위 저장소의 줄 끝 규칙을 덮습니다(B).
  - 이미 있는 Windows 체크아웃은 `.gitattributes`를 담은 커밋을 받아도 git이 바뀌지 않은 작업 파일을 다시 쓰지 않아 CRLF로 남고,
    거기서 빌드한 wheel과 `env.kit_sha256`은 다른 플랫폼과 다릅니다. 한 번 다시 체크아웃합니다([기여 가이드](../CONTRIBUTING.md#현재-개발-검증)).
  - 오래된 Windows 편집기는 LF 파일을 한 줄로 보일 수 있습니다(실행하지 않음). 실제 altair 차트, Windows 클라이언트 판, 사람이 두
    기계 사이에서 git으로 프로젝트를 옮기는 인계는 실행하지 않았습니다. 측정한 Windows 구성은 F의 하나입니다.
- aelix 한 세션의 프로젝트 전환. 한 확장이 두 프로젝트를 번갈아 다룰 때 각 도구 호출의 journal·telemetry·맥락 블록은 그때
  활성인 프로젝트의 것이고 다른 프로젝트의 파일은 바뀌지 않았습니다(시험, aelix 0.1.0b2). 도구 호출이 도는 중에 `/analytics use`로
  바꾸면 그 호출의 telemetry는 끝날 때 활성인 프로젝트에 기록되고 시작한 프로젝트에는 남지 않습니다(시험이 고정합니다.
  이전 구현과 같습니다. [#32](https://github.com/handochan/aelix-analytics/issues/32)).
- Windows의 한계. [측정 기록](verification/2026-09-24-issue-8-platforms.md)과 [Windows 인벤토리 기록](verification/2026-10-02-issue-8-windows-inventory.md)에서 실행한 것과
  추론한 것을 구별해 적습니다. 측정한 Windows 구성은 F에 있는 하나입니다. 긴 경로·콘솔·환경의 한계는 끝의
  [#38](https://github.com/handochan/aelix-analytics/issues/38) 항목들입니다.
  - (측정) `taskkill /T`는 부모가 이미 끝난 자손에 닿지 않습니다. 그런 자손이 파이프를 쥐고 있으면 확장은 정해진 시간만
    기다리고 돌아오며 자손은 남습니다. 확장은 이를 정리 성공으로 적지 않고 stderr에 남깁니다.
  - (관찰과 시험) `taskkill /T`는 트리를 먼저 나열한 뒤 끝내므로, 그 사이 스스로 끝난 프로세스(먼저 끝난 자식을 따라 끝난
    런처 등)를 끝내지 못했다며 실패할 수 있습니다(관찰: Windows CI run 37330266186, 종료 코드 255, "There is no running
    instance of the task"). 그래서 taskkill이 128(래퍼를 찾지 못함, 위 항목)이 아닌 0이 아닌 코드로 끝나면, 확장은 그
    출력(stdout과 stderr)을 숫자로만 읽습니다. 이유 문구는 시스템 언어로 쓰이므로 읽지 않습니다. `/T`에서 taskkill은 다룬
    프로세스마다(래퍼 포함) 그 pid와 부모 pid를 적은 줄을 남기고, 끝내지 못한 것에는 이유 한 줄을 덧붙입니다. 출력이 모두
    이런 줄이고, 그 가운데 래퍼의 pid가 있고(taskkill이 이 트리를 훑었다는 뜻), 적힌 프로세스가 — 끝냈다고 보고한 것까지,
    taskkill은 끝나기를 기다리지 않으므로 — 모두 끝났는지 2초까지 다시 보아 모두 끝났으면 실패로 알리지 않습니다. 확장
    자신의 pid는 빼고 봅니다. 래퍼의 부모라 taskkill이 래퍼의 줄에 적지만 트리에 들지 않고, 이 판정을 하는 동안 늘 돌고
    있기 때문입니다. 출력에 그런 줄이 아닌 것이 있거나(숫자를 담았어도 프로세스를 가리키지 않는 호출 전체의 실패 등),
    래퍼가 적히지 않았거나, 적힌 프로세스가 하나라도 남았거나 볼 수 없으면(다른 사용자의 프로세스) 전처럼 stderr에 남기고
    래퍼를 직접 끝냅니다. 다시 보는 것은 taskkill이 적은 프로세스뿐이고 트리 전체가 아닙니다. taskkill이 찾지 못한 자손은
    위 항목의 한계 그대로이고, 이렇게 넘긴 실패는 taskkill이 성공했을 때와 같은 만큼만 보장합니다. stderr 줄의 형식은 그
    CI run에서 관찰했고, stdout 줄의 형식과 래퍼도 제 줄을 받는다는 것은 알려진 taskkill 출력에서 추론했습니다(추론이
    틀리면 경고가 남는 쪽으로 갑니다). 이 판정은 taskkill의 출력과 프로세스 표를 흉내 낸 시험으로 고정했습니다. 시험의
    stderr는 그 CI run의 것 그대로이고, stdout은 남아 있지 않아(확장은 stderr만 알립니다) 그 형식대로 지었습니다.
    Windows에서 같은 경쟁을 일부러 일으켜 측정하지는 않았습니다([#38](https://github.com/handochan/aelix-analytics/issues/38)).
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
  - (추론) Windows의 제한 시간도 Python 스레드가 SIGINT로 올리므로, 위 두 경우에 같은 이유로 닿지 않는다고 봅니다.
    실행하지 않았습니다.
  - 강제 종료는 `cancelled`가 아니라 `incomplete`입니다(D1).
  - (추론) 교체 중인 기록을 그 순간 여는 reader는 거절될 수 있고, 그러면 그 기록을 없는 것으로 읽습니다.
  - (추론) SIGINT가 무시·기본 동작인지 확인하는 것과 올리는 것은 두 호출이라, 그 사이에 스크립트가 기본 동작으로 바꾸면
    프로세스가 끝납니다.
  - aelix가 끝날 때 아직 멈추는 중인 명령은 그 뒤 강제로 끝내지 않습니다. 요청 디렉터리는 kit이 볼 수 있도록 임시
    디렉터리에 남습니다(POSIX에서는 SIGINT를 이미 보낸 상태입니다).
  - [#38](https://github.com/handochan/aelix-analytics/issues/38)의 1–8번. 베타까지 고치지 않습니다. 2026-10-03 소유자 결정
    ([#39](https://github.com/handochan/aelix-analytics/issues/39), [베타까지의 플랫폼 보강](BETA-QUALITY-GATES.md#베타까지의-플랫폼-보강))에
    따라 B 단계 PR이 그 코드를 건드리거나 지원 구성의 시나리오에서 기록 손실이 새로 확인될 때만 다룹니다. 항목마다 무엇이
    일어나는지, 측정한 구성(추론이면 그 근거), 기록을 잃는지, 대신 할 일을 적습니다. 사용자 안내는
    [README](../README.md#windows에서-쓸-때)에도 있습니다. 측정은 PR #35에서 했습니다(측정 커밋 `4eb0955`, kit 0.1.0; 앞선 실행은
    항목에 적음). 그 뒤 이 항목을 고친 변경은 없습니다. 항목은 지금 이름으로 적습니다: 측정 때의 `/lab`·`/lab note`·`lab.save`·
    `LAB_PROJECT`는 지금의 `/analytics`·`/analytics record`·`analytics.save`·`AELIX_ANALYTICS_PROJECT`입니다. 콘솔 항목은 사람이
    누른 키가 아니라 의사 콘솔의 입력과 도우미 프로세스의 콘솔 이벤트로 측정했고, 경우마다 한 번 실행했습니다.
  - (측정, #38-1) 프로젝트 루트가 MAX_PATH(260)를 넘으면 그 루트를 작업 디렉터리로 하는 프로세스는 시작하지 않습니다
    (CreateProcess, WinError 267). 확장이 kit을 자식으로 시작하는 일(`run_kit`)과 환경 동기화는 `spawn_failed`로 답합니다. 그래서
    `analytics_query`·`analytics_import`·`analytics_run`·`/analytics render`와, 리포트가 아직 없을 때의 `/analytics open`도
    `spawn_failed`로 답합니다(이 명령들이 `run_kit`을 거친다는 것은 코드에서 읽음). `/analytics init`은 `lab.json`을 만들고
    프로젝트를 활성화한 뒤 `spawn_failed`로 답합니다. `analytics_init` 도구와 `/analytics demo`도 같은 순서로 만들고 활성화한 뒤
    환경 동기화에서 `spawn_failed`로 답합니다(코드에서 읽음). `/analytics record` 뒤의 리포트 빌드는 알리지 않고 시작하지 않습니다.
    `/analytics record`와 맥락 블록은 aelix 프로세스 안에서 돌아 됩니다. `analytics_record` 도구도 같은 기록과 리포트 예약을
    거치므로 기록은 되고 리포트는 알리지 않고 다시 만들어지지 않습니다(코드에서 읽음).
    구성: LongPathsEnabled=1, 루트 300자. 루트가 그 환경의 `python.exe`를 MAX_PATH 안에 둘 만큼 짧으면(루트 230자, 계산한
    인터프리터 경로 255자; 이 시험의 kit은 루트 밖에서 체크아웃의 인터프리터로 돌았습니다) run이 260자를 넘는 파일을 써도
    기록·리포트가 됐습니다(출력 312자, 차트 파일 344·353자, 가장 깊은 파일은 코드 사본 365자).
    기록: 잃지 않습니다. 시작하지 못한 kit 명령은 아무것도 실행하지 않고, `/analytics record`의 기록은 journal에 남습니다. 갱신되지
    않는 것은 리포트(파생물)입니다.
    대신: 프로젝트를 짧은 경로에 만듭니다. 이미 그런 루트이면 더 짧은 디렉터리에서 설치본 `analytics`를 부릅니다. 런처는 uv를
    작업 디렉터리 없이 시작하므로, 짧은 디렉터리에서 루트를 디렉터리 인자로 준 `analytics init <루트>`(실제 uv 동기화, 환경의
    인터프리터 경로 325자)와 같은 곳에서 `AELIX_ANALYTICS_PROJECT`를 그 루트로 둔 `analytics status`가 됐습니다(설치본
    `analytics.exe`). 프로젝트 변수를 쓴 것은 `status`뿐입니다. `init`은 그 변수가 아니라 디렉터리 인자로 대상을 받습니다(인자가
    없으면 거절). 그 밖의 명령은 이 길로 실행하지 않았습니다. 그 루트를 작업 디렉터리로 한 프로그램은 같은 오류로 시작하지
    않았습니다(측정한 것은 Python 하나).
    깊은 루트를 지원할지 분명한 문구로 거절할지는 #38의 미결정이고, 동결로 베타까지 그대로입니다.
  - (측정, #38-2) LongPathsEnabled=0이면 Python이 260자를 넘는 경로에 파일을 만들지 못합니다. 루트 230자에는 프로젝트를 만들 수
    없었습니다(앞선 실행 36986490189; 맥락 사본 `.lab/context/…`이 230+52자로 260을 넘는다는 것은 계산). 만들 수 없는 300자
    루트에 kit의 `init`은 JSON 줄 없이 traceback으로, `/analytics init`은 `internal error`로 끝났습니다(만들 수 없는 경로로의
    `init`이 traceback인 것은 모든 플랫폼이 같습니다). 루트 203자에서는 프로젝트 만들기·데이터 가져오기·리포트가 됐습니다. 이
    경로의 kit은 체크아웃의 인터프리터로 돌아 그 깊이에 프로젝트의 uv 환경을 만들지 않았습니다. 루트 아래의 프로젝트 환경
    (`.venv`)의 파일도 200자 안팎의 루트에서는 260자를 넘을 것으로 봅니다(추론, 실행하지 않음). 그 루트에서 260자를 넘는 출력을
    쓰는 run은 kit이 Python으로 쓰는 차트 파일(오류에 적힌 경로 279자)을 만들지 못해(`FileNotFoundError`) `error`로 기록됐습니다.
    그 앞에서 `analytics.save`가 쓴 285자 경로의 데이터셋은 디스크에 있었습니다. `analytics.save`는 DataFrame을 Polars로 쓰고(코드에서
    읽음), Polars는 그 경로에 파일을 만들었습니다. 그러나 run의 출력 목록은 비어 있었고, 그 데이터셋의 누락을 알리는 경고는
    없었습니다. run의 경고 둘은 차트에 관한 것이었고(`error`인 run의 차트는 리포트 갤러리에서 빠진다는 것, 차트의 데이터가
    기록되지 않았다는 것), `finalize_errors`는 없었습니다.
    구성: 레지스트리를 0으로 바꾼 뒤 새 프로세스에서 0으로 읽힘(러너의 값은 1).
    기록: 출력 기록을 잃습니다. 그 파일은 어느 run의 출력으로도 기록되지 않습니다. kit은 출력을 실행 전후의 파일 목록과 각
    파일의 상태로 찾고, 상태를 읽지 못한 파일은 말없이 건너뜁니다(코드에서 읽음). 그 파일은 목록에는 들지만 Python이 260자를
    넘는 그 경로의 상태를 읽지 못해 빠졌다고 봅니다(`os.stat`의 실패 자체는 관찰하지 않았습니다). 그래서 run이 `ok`로 끝나도
    그런 파일은 출력 목록에서 빠진다고 봅니다(추론).
    대신: 0이면 run이 쓰는 모든 경로(`data/derived/…`, `runs/<id>/charts/…`)가 260자 안에 들게 루트와 스크립트·데이터셋·차트
    이름을 짧게 둡니다. 관리자가 LongPathsEnabled를 1로 둘 수도 있습니다. 1에서는 1번의 루트 230자 run이 312자 경로의 출력과
    344·353자의 차트 파일을 기록했고(가장 깊은 파일은 365자의 코드 사본), 루트가 MAX_PATH를 넘으면 1번은 남습니다. 이미 이렇게
    끝난 run의 파일은 기록되지 않은 파일이니, 경로를 줄여 다시 실행해 기록을 남깁니다. 고치지 않는 기록 손실이라 README에도
    적었고, 동결로 베타까지 그대로입니다.
  - (측정, #38-3) 콘솔의 Ctrl+Break(SIGBREAK)는 uv를 곧바로 `0xC000013A`로 끝내므로 설치본 `analytics`가 kit의 기록보다 먼저
    돌아옵니다: 셸에 돌아온 순간 그 run은 `running`이고, kit은 나중에 `cancelled`를 기록하며(자는 kit은 그 잠이 끝난 뒤),
    kit의 결과 줄은 프롬프트 뒤에 나옵니다(의사 콘솔에서 읽음). 프롬프트 뒤에 시작한 run은 첫 run이 끝난 뒤에야
    시작했습니다(첫 run의 끝보다 0.7초 뒤): kit이 끝날 때까지 프로젝트의 run 잠금을 쥐고 있기 때문이라고 봅니다. kit이 끝난
    뒤 남은 프로세스는 없었습니다.
    구성: 의사 콘솔에서 3.12·3.11 설치본, 고전 콘솔에서 3.12 설치본.
    기록: 잃지 않습니다(`cancelled`). 다만 프롬프트가 돌아온 때의 기록은 아직 `running`입니다.
    대신: 콘솔의 취소는 Ctrl+C입니다. Ctrl+Break를 눌렀으면 kit의 결과 줄이 나올 때까지 콘솔을 닫거나 같은 프로젝트의 다음 run을
    시작하지 않습니다. 그 사이에 콘솔을 닫으면 4번처럼 된다고 봅니다(추론). 동결로 베타까지 그대로입니다.
  - (측정, #38-4) run이 도는 중에 의사 콘솔을 닫으면(`ClosePseudoConsole`, 콘솔 닫기 이벤트) 기록은 `incomplete`로 남았습니다.
    남은 프로세스는 없었습니다. kit의 처리기가 돌았는지는 관찰하지 않았습니다.
    구성: 의사 콘솔, 3.12 설치본, 쉬지 않고 도는 스크립트. 고전 콘솔의 창을 닫는 것과 사람이 창을 닫는 것은 실행하지 않았고,
    같다고 봅니다(추론).
    기록: 그 run의 기록을 잃습니다. 기록은 run을 시작할 때 쓴 `running`(제목, 스크립트와 그 스냅샷, 시작 시각)에서 끝나 입력·출력·
    차트·오류가 없습니다(코드에서 읽음). 강제 종료된 run처럼 되돌리거나 고치지 않습니다.
    대신: run이 도는 콘솔을 닫지 않습니다. 멈출 때는 Ctrl+C를 누르고 결과 줄을 기다립니다. 닫혔으면 스크립트를 다시 실행합니다.
    프로세스가 모두 끝났으므로 run 잠금도 풀린다고 봅니다(추론, 닫은 뒤의 run은 실행하지 않았습니다). 고치지 않는 기록 손실이라
    README에도 적었고, 동결로 베타까지 그대로입니다.
  - (측정, 한 번, #38-5) 스크립트가 자식을 시작하는 바로 그때(`CreateProcess` 안) 콘솔의 Ctrl+C가 오면 run은 `cancelled`로
    기록되고 런처는 3으로 끝나지만, 그 Ctrl+C 뒤에 콘솔에 붙은 자식은 그것을 받지 못해 run이 끝난 뒤에도 남았습니다(자식 하나를
    시작한 스크립트 뒤에 python.exe 프로세스 둘이 남음).
    구성: 앞선 실행 36993060072(`2b69cf1`)의 의사 콘솔, 3.12 설치본, 120초 자는 Python 자식. POSIX에서는 실행하지 않았습니다.
    기록: run의 기록은 남지만, 남은 자식이 run 뒤에 하는 일은 어느 run의 기록에도 들지 않습니다(측정한 자식은 자기만 했으므로
    그 뒤의 쓰기는 추론).
    대신: 자식 프로세스를 시작하는 스크립트를 Ctrl+C로 멈춘 뒤에는 남은 프로세스를 명령줄로 가려 끝냅니다. PowerShell(Windows
    PowerShell 5.1과 PowerShell 7)에서
    `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, ParentProcessId, CommandLine`으로 보고,
    `CommandLine`이 그 스크립트가 시작한 자식의 명령인 프로세스만 `Stop-Process -Id`로 끝냅니다. Python이 아닌 자식은
    `python.exe` 대신 그 프로그램의 이름으로 찾습니다. aelix나 다른 작업의 프로세스는 끝내지 않습니다. aelix와 설치본
    `analytics`도 Python으로 돌아 이름만으로는 가려지지 않습니다. 이 명령은 Windows에서 실행하지 않았습니다(개발 기기의 pwsh로
    구문만 확인). 고치지 않는 기록 손실이라 README에도 적었고, 동결로 베타까지 그대로입니다.
  - (추론, #38-6) 콘솔의 시간 창. 두 번째 Ctrl+C가 CRT·CPython이 처리기를 다시 설치하는 짧은 창에 오면 kit(기록은 `running`으로
    남아 `incomplete`)이나 런처가 끝날 수 있고, uv가 자기 처리기를 설치하기 전에 온 Ctrl+C는 Ctrl+Break처럼 uv를 끝내며,
    kit이 import하는 동안 온 Ctrl+C는 run 없이 traceback으로 끝납니다(POSIX에서 130을 관찰, Windows는 `0xC000013A`로 예상).
    기록: 첫 경우에 kit이 끝나면 4번처럼 그 run의 기록을 잃습니다. 둘째는 3번처럼 프롬프트가 kit의 기록보다 먼저 돌아오고(그때
    uv가 kit을 아직 시작하지 않았으면 run이 없습니다), 셋째는 run을 만들지 않습니다.
    대신: Ctrl+C는 한 번 누르고 결과 줄을 기다립니다. 결과 줄 없이 끝났으면 `analytics status`로 그 run의 상태를 봅니다.
    추론으로 남긴 이유: 창이 짧아 하네스가 그 순간에 이벤트를 맞출 방법이 없고, 콘솔 측정은 경우마다 한 번이었습니다. 동결
    동안에는 그 창을 겨냥하는 하네스를 만들지 않고, 베타까지 그대로입니다. POSIX 쪽의 같은 창과 늦은 두 번째 Ctrl+C는
    [#32](https://github.com/handochan/aelix-analytics/issues/32)에 있습니다.
  - (측정, #38-7) PowerShell이 `analytics status`의 출력을 변수에 담으면(`$o = & analytics.exe status`) 그 UTF-8 바이트를 콘솔
    코드 페이지로 읽어 한국어가 깨집니다: 코드 페이지 437과 949에서 그랬습니다. 콘솔에 바로 쓰일 때는 바릅니다(F). cmdlet에
    파이프로 넘겨 문자열로 받을 때도 같다고 봅니다(추론; 개발 기기 macOS의 pwsh 7.6.5에서 437로 두고 `ForEach-Object`·
    `Select-String`으로 받으면 깨졌습니다). PowerShell 7.4부터 외부 프로그램끼리의 파이프와 `>` 리디렉션은 바이트를 그대로
    넘깁니다(PowerShell 문서 about_Redirection; 개발 기기의 pwsh 7.6.5에서 437로 두고도 바이트가 같았습니다).
    구성: pwsh 7.6.6, 의사 콘솔. Windows PowerShell 5.1은 실행하지 않았습니다.
    기록: 잃지 않습니다. kit이 쓰는 바이트와 기록은 UTF-8 그대로이고, 깨지는 것은 PowerShell이 읽은 글뿐입니다.
    대신: 받기 전에 같은 세션에서 `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()`를 둡니다. PowerShell은 외부
    프로그램의 출력을 이 값으로 읽고, 이 값을 바꾸면 그 콘솔의 출력 코드 페이지도 바뀝니다(PowerShell 7.6과 .NET의 소스에서
    읽음). kit의 출력은 바뀌지 않습니다. PowerShell 안에서 실행한 `chcp 65001`은 .NET이 이미 읽어 둔 값을 바꾸지 않으므로 효과가
    없을 수 있습니다(같은 소스). 이 해결은 Windows에서 실행하지 않았습니다(추론). 개발 기기 macOS의 pwsh 7.6.5에서 이 값을
    437로 두면 캡처가 측정과 같은 글자(`∞╜ÿ∞åö δ╢ä∞ä¥`)로 깨지고, 이 줄 뒤에는 바르게 읽혔습니다. 출력 인코딩의 결정은 #38의
    미결정이고, 동결로 베타까지 그대로입니다.
  - (추론, #38-8) Windows에서 확장이 kit에게 넘기는 환경은 플랫폼의 기본 변수(`SystemRoot`·`TEMP`·`USERPROFILE`·`APPDATA` 등)와
    허용 목록(`PATH`·`HOME`·`USER`·`LOGNAME`·`LANG`·프록시 등과 비밀이 아닌 `UV_*`)뿐입니다. 그래서 `USERNAME`·`HOMEDRIVE`·
    `HOMEPATH`와 사용자가 둔 `PYTHONUTF8` 등이 빠집니다(코드에서 읽음). `getpass.getuser()`는 `LOGNAME`·`USER`·`LNAME`·`USERNAME`
    순서로 읽고 없으면 `pwd`를 찾으므로, 이를 부르는 스크립트는 `analytics_run`에서 실패하고 `analytics run`(런처는 호출자의 환경을
    넘깁니다)에서는 될 수 있습니다. `PYTHONUTF8`에 기대어 인코딩 없이 텍스트를 여는 스크립트는 `analytics_run`에서 로캘의
    인코딩으로 읽을 수 있습니다.
    기록: 잃지 않습니다. 실패한 run은 `error`로 기록됩니다. 다만 인코딩 없이 텍스트를 여는 스크립트는 실패하지 않고 로캘의
    코덱으로 다른 글자를 읽은 채 `ok`로 끝날 수 있습니다(추론). 그 run은 기록되지만 결과가 틀립니다.
    대신: aelix를 시작하기 전에 `USER`를 둡니다(PowerShell: `$env:USER = $env:USERNAME`). 확장은 `USER`를 넘기고
    `getpass.getuser()`는 그것을 `USERNAME`보다 먼저 읽습니다. 텍스트 파일은 `encoding="utf-8"`처럼 인코딩을 적어 엽니다.
    추론으로 남긴 이유: aelix 통합 시험은 Windows에서도 `analytics_run`을 부르지만, 그 스크립트에 사용자 이름이나 `PYTHONUTF8`을
    읽는 것이 없습니다. 동결 동안 그 시험을 더하지 않습니다. 확장의 환경 허용 목록은 #38의 미결정이고, 베타까지 그대로입니다.
- 작업 디렉터리의 같은 이름. `python -m`은 작업 디렉터리를 `sys.path`의 맨 앞에 둡니다. 그래서 kit을 부르는 작업 디렉터리(확장은
  프로젝트 루트, `analytics`·`bin/analytics`는 부른 곳)에 `aelix_analytics/` 디렉터리가 있으면 그것이 실행 루트보다 먼저 import됩니다.
  이전의 `labkit/`도 같았습니다.
- 설치본과 사본(ADR-0004).
  - 사본 하나만 설정하는 확인(A)은 두 사본이 모두 이 버전 이상일 때만 됩니다. 이 확인이 없는 이전 버전의 체크아웃을
    aelix의 extensions 디렉터리에 symlink한 채 설치본을 더하면 두 사본이 모두 설정되어, 도구 호출마다 telemetry가 두 번
    기록되고 맥락 블록이 두 번 붙습니다. 설치본을 쓰면 symlink를 지웁니다(README).
  - aelix를 다시 설치하면 그 환경에 더한 이 wheel이 빠집니다: `uv tool install --force aelix`는 측정했고, 같은 명령을
    `--with` 없이 실행하는 `install.sh`는 추론입니다. 다시 설치해야 합니다.
  - wheel을 zip import로 쓰는 설치는 지원하지 않습니다. `kit_dir()`이 거절합니다: zip 안의 kit도 자식에서 실행되지만
    `env.kit_sha256`에 빈 입력의 해시를 기록하고, aelix 프로세스는 `core`를 파일로 로드합니다. 실행 중인 run이
    import하는 kit을 그 자리에서 업그레이드하는 경우는 시험하지 않았습니다.
  - `analytics`라는 이름의 다른 명령이 PATH에 있으면 PATH 순서가 어느 것이 도는지 정합니다. 체크아웃의 `bin/analytics`와
    설치본의 `analytics`가 한 프로젝트를 번갈아 다루면 run마다 `env.kit_sha256`이 다를 수 있습니다.
  - (추론) CPython 3.11 호스트의 `asyncio.wait_for`는 기다리던 자식이 끝나는 바로 그 순간 온 취소를 삼킵니다. 그 도구
    호출은 취소 대신 결과를 돌려주며, 자식의 기록은 이미 끝나 있습니다. aelix가 그 결과를 어떻게 다루는지는 확인하지
    않았습니다.
  - 설치본을 업그레이드한 뒤 aelix를 다시 시작하지 않으면 aelix 프로세스는 이전 확장·`core`를, kit의 자식은 새 kit을
    씁니다. `/reload`로는 바뀌지 않습니다. kit의 버전은 run 기록(`env.kit_version`·`kit_sha256`)과 `lab.json`(만들 때의
    버전)에만 적힙니다. journal 이벤트와 데이터셋 메타데이터에는 버전이 없으므로, 그동안 aelix 프로세스의 이전 `core`가
    쓴 journal 이벤트(`/analytics record`·`analytics_record`, 맥락 동기화)는 기록으로 구별할 수 없습니다. 업그레이드한 뒤에는 aelix를 다시
    시작합니다.
  - 런처의 신호 처리기는 `main()`이 시작할 때 설치됩니다. 인터프리터가 시작하는 동안 온 Ctrl+C는 traceback이나
    SIGINT로 런처를 끝낼 수 있습니다(시작한 자식은 아직 없습니다). 실행하지 않았습니다. Windows에서 자식을 시작하는 순간
    온 Ctrl+C를 다시 보내지 않는 것은 F에 있습니다.
  - 에이전트가 aelix의 bash 도구로 설치본의 `analytics`를 부르면 그 기록은 작성자 `human`으로 남고, 확장의 telemetry도 이를
    따로 표시하지 않습니다(체크아웃의 `bin/analytics`도 이미 그렇습니다).
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
    설치 환경과 측정하지 않은 패치 릴리스. 저장소 CI는 GitHub 호스트 러너의 이미지 하나씩(Windows는 관리자이며 Developer Mode가
    켜진 `windows-2025`)만 쓰고, 러너의 설정 가운데 바꿔 가며 측정한 것은 LongPathsEnabled뿐입니다. 다른 판·이미지·계정·설정과
    아키텍처는 이번 측정에 넣지 않았습니다. 배포본의 설치 시험은 선언한 하한(3.11)과 3.12에서만 돌고, 3.13·3.14는 개발 기기의
    저장소 시험만입니다.
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
  - 위의 항목에서 "실행하지 않았습니다"로 적은 나머지: Windows에서 run이 UTF-8이 아닌 이름의 파일을 읽거나 쓰는 것, 자기 프로세스 그룹이거나 콘솔 없이 시작했거나 Ctrl+C를 무시하는 자식을
    기다리는 run에 오는 콘솔의 Ctrl+C, POSIX에서 자식을 시작하는 순간의 Ctrl+C, `[Console]::OutputEncoding`을 UTF-8로 둔
    PowerShell의 캡처, 확장의 자식 환경에서 빠지는 변수(`getpass`), 런처의 콘솔 출력: 이 PR이 그 경우의 시험을 두지 않았고,
    후속(#36·#38·#32)이 다룹니다.
  - aelix의 `install.sh`: 이 저장소의 시험은 aelix를 `uv tool install`과 `uv run --with`로만 설치합니다(`install.sh`가 이
    wheel을 빼는 것은 위의 추론).
  - Windows에서 다른 프로세스가 보내는 SIGTERM: Windows에는 처리기에 SIGTERM을 보낼 방법이 없습니다.
  - Developer Mode만 켜거나 관리자 권한만으로 만든 링크, `mklink`·`New-Item`으로 만든 링크: 시험은 관리자이며 Developer Mode가 켜진
    러너에서 `os.symlink`와 `_winapi.CreateJunction`으로 링크를 만들었습니다(조상 디렉터리를 가리키는 junction도 kit 0.5.0의 시험이
    만듭니다).
