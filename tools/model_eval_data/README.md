# 실제 모델 평가 (Issue #40)

[`tools/model_eval.py`](../model_eval.py)가 실제 모델로 평가 과제를 실행하고 정답 파일로 채점한다. [베타 품질 기준](../../docs/BETA-QUALITY-GATES.md)
§3의 "12개 과제 × 3회 × 두 모델"(Q2·Q3·Q5·Q11·Q12·Q13) 측정을 준비한다. 캠페인 실행과 결과표는 #24의 일이다. 데이터는 #41의
[평가 fixture](../eval_fixtures_data/README.md)(seed 0)만 쓴다.

## 파일

| 파일 | 내용 |
|---|---|
| [`tasks.json`](tasks.json) | 과제 12개: 사용자 요청 글, fixture, 준비 방식, 게이트, Q11 요청 유형, Q5 조건, 제한 시간. 소유자가 과제 목록을 확인했다(`status: confirmed`, 2026-10-18). 하네스는 이 값을 읽지 않는다 |
| [`answers/`](answers/) | 과제별 정답: 바른 완료(글), 근거 ID, 채점 검사, 쓴 fixture의 seed. 에이전트가 쓴 초안을 소유자가 모두 확인했다(`status: confirmed`, 2026-10-17). T12는 `always_review`(아래 채점) |
| [`models/`](models/) | 모델 설정: [로컬 Qwen](models/qwen3.8-27b-local.json), [프론티어](models/gpt-5.6-sol.json), [Q17-b의 gpt-6.1-sol](models/gpt-6.1-sol.json), [OpenRouter의 Qwen](models/qwen3.8-27b-openrouter.json), [dry-run](models/dry-run.json) |
| [`conditions.json`](conditions.json) | 도구 표면(Q11), 제안 조건(Q5), 글 판정에 쓰는 낱말 묶음 |
| [`dry_run/`](dry_run/) | dry-run의 가짜 모델이 따르는 스크립트(과제별, 없으면 상태 확인 뒤 답 하나) |

정답은 에이전트가 fixture를 읽고 쓴 초안이었고, 소유자가 12개를 모두 확인했다(2026-10-17, #40): `status`는 `confirmed`, `confirmed_by`는 확인한 사람이다.
초안 정답의 점수는 확정 결과가 아니다(run 기록의 `answer.status`). 정답을 바꾼 뒤에는 `score`로 이미 실행한 run을 다시 채점한다.

## 과제

| ID | fixture | 게이트 | 무엇을 보나 |
|---|---|---|---|
| T01 | S | Q2·Q12 | 새 세션에서 목표·핵심 질문·다음 할 일·열린 질문(닫힌 질문을 열린 것으로 말하지 않음) |
| T02 | S | Q2 | 정정된 발견(e41 → e287)과 철회된 결정(e68 → e274) |
| T03 | S | Q2·Q3 | 최근 목록 밖의 오래된 중요한 발견(e24) |
| T04 | S | Q3 | 맥락 블록에서 빠진 CONTEXT 섹션, 4,000자에서 잘린 기록(e74) |
| T05 | S | Q2·Q3 | 덮어쓴 표: 지금 바이트는 뒤의 run(032539)의 것, 첫 run의 원래 표는 그 run의 차트 데이터로 같은 바이트가 남아 있음 |
| T06 | Q10 | Q11·Q3 | 정답 도구가 하나인 요청(영어) |
| T07 | Q10 | Q12·Q5 | 분모가 틀린 전환율: 정의 정정과 기록 |
| T08 | Q10 | Q12·Q5·Q13 | 부분 월 비교: 같은 일수끼리 다시 비교 |
| T09 | Q10 | Q12·Q2 | 두 지표의 값이 둘씩: 집계 키·단위 |
| T10 | Q13 | Q13·Q2 | 기록된 주장 넷을 저장된 수치로 판정(뒷받침·거짓·판단 불가) |
| T11 | Q13 | Q2·Q3 | 실패한 run의 부분 산출물과 지금 바이트의 생산자 |
| T12 | Q13 입력 | Q13·Q11·Q5 | 새 프로젝트에서 모호한 EDA → 스텝 프로파일·점수 → 모든 센서 그림(요청 3개) |

요청 글은 도구 이름을 말하지 않아 도구 표면과 이름 전환 전후(#9)에 같은 글을 쓴다. 모델에게는 fixture의 `project/`와 `input/`만
준다(`manifest.json`·`expected.json`은 정답을 담음). 모델이 일하는 작업 루트는 `OUT` 밖의 새 디렉터리라 그 위로 올라가도 fixture와
다른 run이 없다(아래 격리).

## 실행

개발 환경에서 실행한다(fixture를 만들려면 분석 라이브러리가 필요하다). aelix는 하네스가 `uv run --isolated --no-project --python 3.12
--with aelix==0.1.0b2 aelix`로 띄운다(`--aelix-command`로 바꿈). aelix 로그인을 쓰는 모델(프론티어)은 같은 환경의 Python으로
[`tools/model_eval_aelix.py`](../model_eval_aelix.py)를 거쳐 띄운다(아래 [프론티어 모델](#프론티어-모델)).

```sh
uv run --locked python tools/model_eval.py validate --fixtures <dir>          # 설정 검사 + 정답의 ID가 fixture에 있는지
uv run --locked python tools/model_eval.py run --model dry-run --task T06 --out <dir>
uv run --locked python tools/model_eval.py serving-check --model qwen3.8-27b-local
uv run --locked python tools/model_eval.py run --model qwen3.8-27b-local --all --repeats 3 --out <dir>
uv run --locked python tools/model_eval.py run --model gpt-5.6-sol --task T06 --out <dir>    # 소유자의 aelix 로그인(openai-codex)
uv run --locked python tools/model_eval.py score <dir>                        # 저장된 run을 지금 정답으로 다시 채점
uv run --locked python tools/model_eval.py scan <dir>                         # 하네스가 끝내지 못한 run을 정리하고 비밀 검사
```

`run`의 선택: `--task`(여러 번) 또는 `--all`, `--repeats N`(반복 번호는 `--first-repeat`부터), `--surface`, `--proposal`,
`--checkout DIR`, `--fixtures DIR`(fixture를 만들고 다시 쓰는 곳, 기본 `OUT/fixtures`), `--work-root DIR`(run마다 작업 루트를 만드는
곳, 기본은 시스템 임시 디렉터리, `OUT` 안은 거절), `--seed`, `--timeout`(초, 기본은 과제의 값), `--script`(dry-run 스크립트),
`--aelix-auth FILE`(로그인을 쓰는 모델의 로그인 파일, 기본은 호출자의 aelix가 쓰는 곳: `$AELIX_CODING_AGENT_DIR/auth.json` 또는
`~/.aelix/agent/auth.json`. 에이전트 디렉터리의 `auth.json`이어야 하고 링크이거나 이름이 둘 이상(하드 링크)이면 거절).
`--out`과 `--work-root`는 `~/.aelix`, 로그인 디렉터리와 그 위 디렉터리 안에 둘 수 없다.
`--out`은 새 디렉터리, 빈 디렉터리, 이전 출력(`summary.json`이 있음) 중 하나다. 이전 출력에는 run을 더한다. 요청한 run 가운데 하나라도
이름이 이미 있으면 첫 run 전에 모두 거절한다(아무것도 실행하지 않음). 정답은 seed 0 fixture의 수·ID로 쓰여 있어 `--seed`가 정답의
`fixture.seed`와 다르면 거절한다(seed마다의 정답은 아직 없음). `summary.json`은 run 도중 하네스가 실패해도 마지막에 다시 쓴다.

종료 코드: 0(요청한 run을 모두 실행·기록함. 과제 실패와 시간 초과는 데이터), 1(어떤 run을 실행하거나 기록하지 못함, 어떤 run의
비밀 검사가 통과하지 못함, `OUT`에 검사하지 않은 run이 남음, 또는 신호로 멈춤), 2(사용법·설정 오류, 아무것도 실행하지 않음),
3(전제가 없어 실행하지 않음: 모델의 aelix 로그인이 없거나 API가 그 로그인을 거절함, 키 없음, 서빙이 고정 설정과 다름, kit에 없는 조건.
`summary.json`에 `not_run`과 이유, 로그인 문제면 로그인 방법과 실행할 명령).

**멈춤과 강제 종료.** SIGTERM·SIGHUP·SIGINT(에이전트의 명령 시간 제한이나 작업 중지가 보내는 것)를 받으면 하네스는 진행 중인 run을
끝까지 닫는다: aelix를 멈추고, 작업 루트를 정리해 옮기고, 모든 파일을 검사하고, 기록을 쓴 뒤 종료 코드 1로 끝난다(정리 중에 온 두 번째
신호는 적어 두기만 하고 검사를 끊지 않는다. 남은 프로세스를 기다리지 않고 바로 멈추게 할 뿐이다). 시간 초과와 멈춤에서 aelix의 프로세스
그룹에는 SIGTERM을 먼저 보내고 100초(aelix의 토큰 요청 제한 30초 × 연결·쓰기·읽기) 뒤에야 SIGKILL을 보낸다: 런처는 토큰 갱신과
로그인 쓰기가 진행 중이면 그것이 끝날 때까지 종료를 미룬다(갱신 응답과 파일 쓰기 사이에 죽으면 소유자의 파일에 이미 쓴 갱신 토큰이
남기 때문). `kill -9`처럼 잡을 수 없는 종료에 대비해 run 기록은 aelix를 띄우기 전부터 디스크에 있다(`provisional`, `secret_scan:
not_scanned`, 작업 루트 경로 포함). 로그인을 쓰는 모델은 런처가 하네스와 파이프(lifeline)로 이어져 하네스가 사라지면 모델이 띄운
명령과 aelix를 멈추고, 그 명령이 끝나기를 10초까지 기다린 뒤 run을 로그인의 정확한 값으로 스스로 가린다(POSIX, 아래 비밀 검사).
그런 run은 `summary`에 `not_scanned`로 남고(종료 코드 1, `score`는 읽지 않음), `scan OUT`이 남은 프로세스를 멈추고 작업 루트를 옮겨
검사한다(런처의 가림은 보고되지 않으므로 `exact_not_run`으로 실패). 시간 초과·멈춤에서 SIGTERM을 받은 런처는 aelix를 거기서 멈추고
(갱신 중이면 그 뒤에) 하네스가 run을 정리할 때까지 기다렸다가 가린다(`stop.launcher_waiting`).

### 조건

- **도구 표면**(Q11, `--surface`): `default`(aelix 0.1.0b2의 기본 목록 그대로: `aelix_status`·read·bash·edit·write·grep·find·ls와 확장
  6개, 모두 14개 — 관련 없는 유사 도구 포함), `coding`(read·bash·edit·write와 확장 6개), `analytics_read`(read와 확장 6개, bash로 돌아갈
  길이 없음). 선택적 노출·새 도구 같은 대안은 그 확장 파일을 `conditions.json`의 표면으로 더한다.
- **제안 조건**(Q5, `--proposal`, T07·T08·T12): `unrequested`(요청 그대로), `requested`(마지막에 재사용 후보 요청을 더하고 답했는지
  검사), `always_on`(프로젝트의 제안 모드 설정이 필요한데 kit 0.4.0에 없어 `not_run`, #17).
- **이름 전환 전후**(#9, `--checkout DIR`): 다른 checkout의 확장과 kit으로 같은 과제를 돌린다. `extension/aelix_lab.py`가 있으면 옛 이름
  (`lab_*` 도구, `AELIX_LAB_*` 변수)으로 격리한다. 인자 오류·과제 완수·호출 토큰이 같은 형식으로 남아 #24가 비교한다.

## 로컬 Qwen 서빙

[`models/qwen3.8-27b-local.json`](models/qwen3.8-27b-local.json)이 고정한 설정이다(이 Mac: Apple M5 Pro, 64 GiB, 2026-10-07).

| 항목 | 값 |
|---|---|
| backend | ollama 0.35.1, 전용 서버 `127.0.0.1:11435` |
| 모델 | `qwen3.8:27b-mtp-q4_K_M`(ollama 라이브러리, manifest `sha256:22130167…`, GGUF Q4_K_M, 27.3B; 가중치는 `qwen3.8:27b-q4_K_M`과 같고 MTP 초안 매개변수만 다름). 원본 `Qwen/Qwen3.8-27B@1d4bf0f…` |
| context | 131,072(`OLLAMA_CONTEXT_LENGTH`), 적재 크기 18.5 GB |
| 출력 예약 | 32,768(aelix `maxTokens`) |
| thinking | aelix `medium` → `reasoning_effort: medium` |
| 표본 추출 | ollama 모델 매개변수 = Qwen 권장 thinking 값(temperature 1.0, top_p 0.95, top_k 20, min_p 0, presence 0, repeat 1). aelix는 표본 추출 값을 보내지 않음 |
| tokenizer | GGUF에 든 것, 토큰 수는 ollama의 usage(`cached_tokens` 포함) |

서버는 소유자 앱의 서버(11434)와 따로, 설정의 `server_env`로 띄운다. 모델은 이미 받아 둔 것을 쓴다(없으면 `ollama pull
qwen3.8:27b-mtp-q4_K_M`, 약 17.7 GB).

```sh
OLLAMA_HOST=127.0.0.1:11435 OLLAMA_CONTEXT_LENGTH=131072 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 \
OLLAMA_KV_CACHE_TYPE=f16 OLLAMA_KEEP_ALIVE=30m OLLAMA_NOPRUNE=1 OLLAMA_NO_CLOUD=1 ollama serve
uv run --locked python tools/model_eval.py serving-check --model qwen3.8-27b-local
```

`serving-check`는 ollama 버전, 모델 digest, 적재한 모델의 context를 고정값과 비교한다. 다르면 `run`도 실행하지 않고 `not_run`이다.
aelix는 내장 catalog에 ollama가 없어 하네스가 run의 aelix 디렉터리에 `models.json`(provider `aelix-eval-ollama`, `openai-completions`,
developer role·store·strict 필드를 보내지 않는 compat)을 쓴다.

같은 기기에서 비교한 MLX 빌드(`qwen3.8:27b-mlx`, nvfp4)는 첫 요청의 처리가 빨랐지만 공유 접두사의 캐시를 쓰지 않았다. 지원 하단의 대표로
llama.cpp 계열의 표준 4비트(Q4_K_M)를 고른다.

## 프론티어 모델

[`models/gpt-5.6-sol.json`](models/gpt-5.6-sol.json): OpenAI GPT-5.6 Sol을 aelix 0.1.0b2의 내장 `openai-codex` provider(ChatGPT
backend의 Codex Responses, `https://chatgpt.com/backend-api/codex/responses`)로 부른다. 인증은 API 키가 아니라 소유자가 aelix에서
`/login`(OpenAI Codex)으로 해 둔 로그인이다(소유자 결정 2026-10-07, 갱신). 소유자의 로그인으로 `aelix --list-models`가 보이는
행은 `openai-codex gpt-5.6-sol 1.1M 128K`다(catalog: context 1,050,000, 최대 출력 128,000, 입력 글·그림, thinking 있음).

| 항목 | 값 |
|---|---|
| provider / model / API | `openai-codex` / `gpt-5.6-sol` / `openai-codex-responses` |
| thinking | `medium`(소유자 결정이 아니라 aelix 0.1.0b2의 기본값 `DEFAULT_THINKING_LEVEL`, `core/defaults.py`) → aelix가 `reasoning: {effort: medium, summary: auto}`를 보냄(고를 수 있는 값: off·minimal·low·medium·high·xhigh) |
| context / 출력 예약 | 1,050,000 / 128,000(aelix catalog, 목록 표시 1.1M / 128K) |
| 요청 | 시스템 지침은 `instructions`, `store: false`, `text.verbosity: low`, `reasoning.encrypted_content`, `parallel_tool_calls`, `prompt_cache_key` = 세션 ID. 표본 추출 값은 보내지 않음 |
| 토큰 | backend usage를 공유 Responses 엔진으로 읽음: 비캐시 입력 = input, 캐시 입력 = cache_read, reasoning(출력의 일부, 없으면 aelix가 0으로 적어 0과 미보고를 가르지 못함), 캐시 쓰기는 `not_measured` |
| 과금 | 로그인한 ChatGPT 구독. catalog의 가격 행(1M 토큰당 입력 5·출력 30·캐시 입력 0.5)은 API의 값이라 이 로그인의 비용이 아님 |

**로그인을 쓰는 방법.** aelix 0.1.0b2는 상태를 에이전트 디렉터리 하나(`AELIX_CODING_AGENT_DIR`, 없으면 `~/.aelix/agent`)에 두고,
명령줄은 자격 증명 저장소를 언제나 `<에이전트 디렉터리>/auth.json`으로 연다(`cli/entry.py`). 저장소만 옮기는 변수나 옵션은 없다
(`AELIX_AUTH_PATH`는 경로 없이 만든 저장소만 읽고, 명령줄은 그렇게 만들지 않는다). 그래서 하네스는 aelix를
[`tools/model_eval_aelix.py`](../model_eval_aelix.py)로 띄운다: aelix 자신의 진입점(`main_sync`)을 그대로 부르되, `<에이전트
디렉터리>/auth.json`으로 여는 저장소 하나만 로그인 파일(`--aelix-auth`, 기본은 호출자의 aelix가 쓰는 곳)로 연다. 나머지(설정,
확장 신뢰, `models.json`, 업데이트 확인, 세션)는 모두 run의 에이전트 디렉터리에 남는다. 토큰이 만료되면 aelix가 그 파일의 잠금을
잡고 갱신해 같은 디렉터리에서 원자적으로 바꿔 쓴다(`auth.json.tmp` → `os.replace`). 소유자의 aelix가 하는 것과 같으므로 갱신된
토큰은 소유자의 것으로 남는다.

- **링크·복사본을 쓰지 않는 이유**: aelix는 갱신한 로그인을 `<에이전트 디렉터리>/auth.json`을 바꿔치워 쓴다. 그 자리가 링크면 링크가
  run 안의 새 파일로 바뀌어(새 토큰이 run에 남음) 소유자의 파일에는 서버가 이미 쓴 갱신 토큰이 남고 로그인이 깨질 수 있다. 복사본은
  처음부터 같다. 런처를 쓰면 run의 에이전트 디렉터리에는 로그인을 담거나 가리키는 파일이 없다.
- **하네스가 하는 것**: 로그인 파일을 열지 않는다. 메타데이터(있는지, 수정 시각, inode, 이름 수)만 본다. 파일이 있는지 보고(없으면 aelix가 잠금 때 빈 파일을 만들기 때문. 런처도 aelix를 부르기 전에 다시 확인한다), 빈 에이전트
  디렉터리(`OUT/iso/login-check-agent`)에서 런처로 `aelix --list-models "openai-codex gpt-5.6-sol"`을 실행해 aelix가 그 모델을
  나열하는지 본다(aelix는 provider의 항목이 있을 때만 나열하고, 토큰을 갱신하거나 출력하지 않는다). 파일이 없거나 항목이 없으면
  run은 `not_run`(`aelix_login_missing`, 종료 코드 3)이고 기록에 로그인 방법(`aelix` → `/login` → OpenAI Codex)과 실행할 명령을 남긴다.
  run마다 로그인 파일의 메타데이터(수정 시각·inode·이름 수)를 run 전, aelix 종료 때, 비밀 검사 뒤 마지막으로 적는다(`login.before`·
  `after_exit`·`final`, `mtime_before`·`mtime_after`. run 중에는 aelix의 갱신이나 소유자의 다른 aelix가 바꿀 수 있고, aelix가 끝난 뒤
  바뀌거나 이름이 둘 이상이면 검사 실패). 로그인 확인의 aelix 오류 문구는 자격 증명 모양을 가린 뒤 기록·출력한다. 로그인 항목은
  있지만 API가 그 로그인을 거절하면(첫 요청이 usage 없이 인증 오류로 끝남: 폐기·만료된 갱신 토큰) 모델의 실패가 아니라 `not_run`
  (`aelix_login_unusable`, 종료 코드 3)이고 로그인 방법과 명령을 남긴다.

**비밀 검사.** 모델 설정이 키 변수(`api_key_env`)를 지정하면(지금 설정에는 없음) 하네스는 키 값을 쓰지 않는다: `command.json`에는
변수 이름만, run 기록은 쓰기 전에 키를 `<redacted>`로 바꾸고, aelix가 끝난 뒤(그 뒤 하네스가 실패해도) run 디렉터리 아래 모든 파일에서
키를 아래 '로그인의 정확한 값으로 가리기'와 같은 형태(런처의 `forms_of`: 원래 바이트, JSON 이스케이프, URL 인코딩, base64,
16진수, UTF-16; 8자보다 짧은 값은 원래 바이트만)로 찾아 같은 길이 표시로 덮어쓴다(`secret_redacted`, Windows 포함: Windows에서 aelix의 bash 도구는 `$SHELL`이 있는
파일을 가리키지 않으면 PowerShell로 돌고, Windows PowerShell 5.1의 `>`는 UTF-16LE로 쓴다). 호출자의 자격 증명 변수(이름에
TOKEN·KEY·SECRET·PASSWORD·CREDENTIAL·COOKIE)의 값도 같은 방식으로 덮어쓴다(그 변수들은 aelix에 넘기지 않지만 모델이 다른 곳에서 읽을 수
있음). 그다음 모든 run에서(`not_run` 포함),
값을 모른 채 run 디렉터리 아래의 **모든 파일**(링크는 따라가지 않음, `run.json` 포함)에서 자격 증명 모양의 글을 찾는다: JWT(`eyJ…` 점
둘), `Bearer` 토큰, 값이 있는 `refresh`·`access`·`*_token`·`api_key`·`client_secret`·`secret`·`password` 항목(앞에 다른 낱말이 붙은
이름 `GEMINI_CLI_IDE_AUTH_TOKEN=`, 따옴표가 이스케이프된 JSON 사건 줄 포함), `sk-` 키, 대·소문자와 숫자가 섞인 32–256자의 무작위
base64url 토큰(이름·코드·`sha256=` digest·codex의 암호화된 reasoning `gAAAAA…`·base64 그림 조각은 빼도록 모양을 가림), 그런 모양의
파일 이름. 찾은 것은 같은 길이 표시로 덮어쓰고 값 없이 파일·줄·종류·길이만 `secret_scan`에 적는다. **파일을 그 자리에서 고쳐 쓰지
않는다**: 고친 내용을 같은 디렉터리의 임시 파일에 쓰고 `os.replace`로 바꾼다(하드 링크로 이어진 다른 이름이 남아 있어도 그 파일에는
쓰지 않음). 검사 전에 run 밖으로 이어진 링크를 lstat만으로 찾아(파일을 열지 않음) 그 이름을 지운다: run 밖을 가리키는 심볼릭 링크,
run 밖에도 이름이 있는 파일(하드 링크: 링크 수가 run 안의 이름 수보다 많음), 로그인 파일과 같은 파일(장치·inode). 작업 루트에서는
옮기기 전에 지운다(다른 파일 시스템으로 옮길 때 복사가 링크된 파일을 읽지 않도록). 이름이 모두 run 안인 하드 링크는 남기고 이름마다
바꿔 쓴다. `secret_scan.status`는 `passed`·`failed`·`not_scanned`이고 `problems`가 이유다: `credential_shapes`(찾음),
`outside_links`·`hard_links`(지운 링크), `unreadable`(읽지 못한 디렉터리·파일: 찾지 않은 것은 통과가 아님), `writers_left`·
`processes_unchecked`(aelix가 끝난 뒤 작업 루트에 쓸 수 있는 프로세스가 남았거나 확인하지 못함), `written_after_scan`(검사가 시작된 뒤
바뀐 파일), `names_login`(`.aelix`·`auth.json`·로그인 파일을 가리킨 도구 호출), `login_changed_after_exit`·`login_has_other_names`
(검사 뒤 마지막으로 잰 로그인 파일의 수정 시각·inode가 aelix 종료 때와 다르거나 이름이 둘 이상), `after_exit_errors`·`scan_error`,
그리고 아래 정확한 값의 `exact_values`·`exact_unreadable`·`exact_error`·`exact_not_run`.
하나라도 있으면 `failed`, `needs_review`에 `secret_scan`(프로세스 문제면 `after_exit`도), 종료 코드 1이다. 검사 결과 자체(파일 이름,
링크 대상: 만든 그대로, Windows의 `\\?\` 접두사 없이)도 가린 뒤 기록하고, 마지막에 쓴 `run.json`을 한 번 더 검사한다. 옮기지 못한 작업 루트는 그 자리에서 검사한다
(`work_root_left`). 모델이 작업 루트 밖에 쓴 파일은 찾지 않는다(그런 경로를 쓴 호출은 `outside_work_root`).

**로그인의 정확한 값으로 가리기(소유자 결정 2026-10-07).** 모양 검사가 놓치는 값(256자보다 긴 값, 점이 든 값, 소문자 16진수,
UTF-16)에 대비해, 로그인을 쓰는 모델의 run은 런처가 끝에 로그인의 **정확한 값**으로 한 번 더 가린다(POSIX). 값을 비교하는 것은
그 값을 이미 메모리에 가진 aelix 자신의 프로세스(런처)뿐이다. 값은 파일·출력·로그·인자·환경·파이프로 나가지 않고 하네스에도
돌아가지 않는다.

- **값**: aelix의 `AuthStorage`가 읽고(`_read_raw`) 쓰고(`_write_raw`, 갱신된 토큰) 돌려준(`get_*api_key`) 것 가운데 provider
  항목의 문자열(`access`·`refresh`·`id_token`·`key` 등). 자격 증명이 아닌 필드(`type`·`token_type`·`scope`·`expires*`·
  `accountId`·`enterpriseUrl`·`uuid`·`email`·`name` 등)와 16자보다 짧은 값(`Bearer`·`oauth` 같은 것)은 비교하지 않는다. aelix가
  해석하는 키(`!명령`, 설정된 변수 이름)는 해석된 값으로 비교한다. 끝날 때 로그인 파일을 런처 안에서 한 번 더 읽는다(소유자의 다른
  aelix가 run 중에 갱신했을 수 있음).
- **형태**: 원래 바이트, JSON 이스케이프(`\/` 포함, 두 번 이스케이프한 것 포함), URL 인코딩(`quote`·`quote_plus`, 대·소문자 16진수),
  base64·base64url(세 가지 정렬 모두: 더 긴 인코딩 안에 든 것도), UTF-8 바이트의 16진수(소·대문자), UTF-16LE·BE. 줄로 나뉜 값(줄바꿈된
  base64·16진수 출력 등), 압축 등 다른 인코딩, 값의 일부는 찾지 않는다.
- **언제**: aelix의 `main`이 돌아오면(또는 SIGTERM으로 멈추면) 런처는 작업 루트에서 나와(`chdir /`) 하네스에 기다린다고 알린다.
  하네스는 작업 루트에서 일하는 프로세스를 기다리고(런처와 uv는 뺌) `.venv`를 지우고 링크를 쓸고 작업 루트를 run으로 옮긴 뒤 첫
  가림(`all`: run 디렉터리 전체와 옮기지 못한 작업 루트)을 시킨다. 그 뒤 기록·채점·모양 검사를 하고, 마지막에 쓴 `run.json`을 한 번
  더 가리게 한다(`record`: 첫 가림 전에 하네스가 알게 된 프로세스 명령줄·링크 대상이 기록에 들어가므로. 기록에 남기는 명령줄은
  공백에서 잘라 값의 일부가 남지 않게 한다). 그다음 런처가 끝난다.
- **쓰기**: 모양 검사와 같다. 링크는 따라가지 않고(값이 든 링크 대상은 가린 대상으로 링크를 새로 만듦), 고친 파일은 임시 파일 →
  `os.replace`, 이름에 든 값은 `redacted`로 바꾼다(이름에는 길이를 남기지 않음). 로그인 파일 자체이거나 run 밖에도 이름이 있는 파일(하드 링크)은 읽지 않고 `unreadable`로 적는다.
  표시는 같은 길이라 파일의 배치는 그대로다(값의 길이는 파일에서 보임).
- **기록**: `secret_scan.exact`에 `status`(`passed`·`failed`·`not_run`·`not_applicable`), `files`, `findings`(바꾼 수),
  `unreadable`, `masked_files`·`renamed`·`links_rewritten`(상대 경로), `values_compared`(비교한 값의 수), `passes`. 값·값의 길이·
  해시는 어디에도 없다. 찾으면 `exact_values`로 검사 실패(`needs_review`, 종료 코드 1). 로그인을 썼는데 가림이 없었으면(Windows:
  런처와의 파이프가 없음, 하네스가 그 전에 죽음, 비교할 값이 없음) `exact_not_run`으로 실패다. 로그인을 쓰지 않는 모델은
  `not_applicable`.

### Q17-b의 gpt-6.1-sol

[`models/gpt-6.1-sol.json`](models/gpt-6.1-sol.json): 갱신된 OpenAI 모델로 Q17-b를 수행하기로 한 소유자 결정(2026-10-21, #23)의
설정이다. provider·API·로그인·thinking은 gpt-5.6-sol과 같다. aelix 0.1.0b2의 catalog에 이 모델이 없어서, 하네스와
[`tools/human_e2e.py`](../human_e2e.py)는 run의 에이전트 디렉터리에 `models.json`(provider `openai-codex`에 모델 항목 하나)을 쓴다.
`api`·`baseUrl`은 적지 않으므로 aelix가 내장 provider의 값을 쓴다(`models_json.parse_models`). context 500,000은 소유자 로그인의
Codex CLI 0.162.0 모델 목록(2026-10-21)의 값이다. 출력 예약 128,000은 그 목록에 없어 gpt-5.6-sol의 catalog 값을 쓴다.
소유자의 로그인으로 `aelix --offline --list-models "openai-codex gpt-6.1-sol"`이 `500K 128K`를 나열했다(2026-10-21, 호출 없음).
§3 캠페인(#24)의 프론티어 모델을 바꾸는 결정은 아니다.

[`models/qwen3.8-27b-openrouter.json`](models/qwen3.8-27b-openrouter.json): Q17-b의 Qwen3.8 27B를 로컬 ollama 대신 OpenRouter로
부르기로 한 소유자 결정(2026-10-21, #23)의 설정이다. aelix 0.1.0b2 catalog의 `openrouter qwen/qwen3.8-27b`(context 262,144, 출력
131,072)를 소유자의 aelix 로그인(OpenRouter 키)으로 쓴다. OpenRouter가 고르는 상위 제공자와 양자화는 고정하지 않는다
(`openRouterRouting` 없음). 그래서 지원 하단의 로컬 기준(`qwen3.8-27b-local`)과 같은 서빙이 아니다.

## run 기록

`OUT/runs/<과제>-<모델>-<표면>-<제안 조건>-r<반복>/`에 `run.json`(아래), `events.jsonl`(aelix `--mode json`의 사건 그대로),
`stderr.txt`, `command.json`(argv·cwd·하네스가 정한 변수·로그인 파일 경로), `agent/`(aelix의 상태와 `models.json`), `work/`(모델이 일한
작업 루트: 프로젝트와 입력, aelix가 끝난 뒤 옮겨 옴), `home/`(run의 HOME)이 남는다. kit의 확장은 요청이 끝날 때마다 보고서 빌드(`uv run
--project <프로젝트> … render --if-changed`)를 분리된 프로세스로 띄워 aelix보다 오래 살고, 모델의 bash 명령은 저마다 새 세션에서 돈다
(aelix의 bash 도구). 하네스는 aelix가 도는 동안 프로세스 목록으로 aelix의 자손을 모으고, aelix가 끝난 뒤 작업 루트에서 일하는
프로세스(명령줄에 작업 루트가 나옴, 작업 디렉터리나 연 파일이 그 안에 있음(Linux `/proc`, 그 밖의 POSIX `lsof`), aelix의 자손)가 없어질
때까지 기다린다(`after_exit`). 자연 종료 대기는 최대 60초, 이후 목록 조회·종료 시도에는 공유된 30초 예산을 쓴다. POSIX는 SIGTERM 후
그 예산 안에서 최대 5초를 기다리고 SIGKILL, Windows는 `taskkill /T /F`로 종료한다. 새 프로세스가 계속 생겨도 예산을 연장하지 않는다.
프로세스 조회와 Windows 종료 명령의 timeout도 남은 예산으로 제한하며, 한 `settle` 호출의 기본 최대 대기는 90초다. 결과의 `timeout_s`·
`stop_timeout_s`·`max_wait_s`·`deadline_reached`와 실제 `waited_s`를 함께 남긴다. 실제로 사라진 것만 `stopped`, 남은 pid는 `left`이고,
`processes`에 가린 명령줄과 판정 근거를 보존한다. 프로세스 목록 조회를 완료하지 못하거나 공유 예산이 끝나면 `checked: false`다.
중단 요청이 이미 있으면 첫 조회부터 자연 종료 대기 없이 30초 예산만 쓴다. Windows의 cwd/open-file 조회 미지원은 기존대로
`cwd_checked: false`이며 명령줄·추적한 자손으로 판정한다. 남은 프로세스나 예산 안의 미완료 목록 조회는 검사 실패이며
종료 코드 1이다. Windows의 HOME 격리 시험은 독립 임시 폴더에서 세 번 실행하고 실패 시 이 결과를 출력한다. 그다음
프로젝트의 Python 환경(`.venv`, kit이 uv로 만든 공개 패키지 약 450 MB와 uv의 Python·캐시로 가는 링크)을 지우고(링크를 따라가지 않음,
`uv.lock`이 남아 다시 만들 수 있음, `environments_removed`) 작업 루트를 옮긴 뒤 같은 한도로 한 번 더 확인한다(`after_exit.after_move`;
두 호출의 기본 대기 예산 합은 180초, run timeout/환경 삭제/이동/파일 검사는 이 예산에 포함하지 않음). 기다린 시간은
`elapsed_s`에 들지 않는다. `OUT/summary.json`과 그것에서 만든 `summary.md`가 run과 (모델, 과제, 표면, 조건)별 집계를 담는다.

- **모델·서빙**: 설정 id, provider, model, API, thinking, backend와 버전, 양자화, digest, tokenizer, context, 출력 예약, 표본 추출, 서빙 확인
  결과, 설정 파일 전체(`configuration`, 서버 환경 변수·가중치 blob·원본 revision 포함)와 그 sha256(정규화한 JSON과 파일 바이트), 실제로
  답한 모델(`observed_models`).
- **호스트·kit**: aelix 버전, 띄운 명령(`launch`, 로그인을 쓰는 모델이면 런처와 그 sha256), Python·플랫폼, checkout 경로·commit·dirty,
  kit 버전, 확장 파일 sha256, 이름 세대. checkout이 자기 저장소의
  최상위가 아니면(다른 저장소 안에 풀어 둔 사본 등) commit·dirty는 `null`이고 이유(`commit_reason`)를 적는다.
- **조건**: 과제, 반복 번호, seed, fixture(종류·생성기 버전·digest), 표면, 제안 조건, 정답 상태, 제공된 도구 목록(dry-run에서 관측, 그 밖엔
  `--tools` 또는 aelix 기본).
- **실행**: 시작·끝·걸린 시간, 종료 코드, 시간 초과, 요청과 답(턴마다), 모델 요청 수, 자동 재시도, 압축, 작업 루트(`work.root`).
  하네스가 aelix가 끝난 뒤 실패하면 `outcome: error`와 `harness_error`를 적은 기록을 남긴다.
- **도구 호출**: 이름·역할·인자·결과 앞부분·`error_class`. 인자 오류는 `unknown_tool`(없는 도구), `schema`(aelix의 인자 검증 실패),
  `argument`(확장이 인자 때문에 거절한 오류 코드)의 합이다. `query_error`(SQL 실행 오류), `tool_error`, `run_failed`(스크립트가 실패한
  분석 run)는 따로 센다. bash로 Python을 돌린 호출(`bash_python`)도 센다. 인자의 경로가 작업 루트 밖을 가리키는 호출(절대 경로, `~`,
  작업 루트 위로 가는 `../`; `/usr`·`/bin` 같은 시스템 프로그램과 기록 글·검색 패턴은 빼고)은 `outside_work_root`에 적고
  `needs_review`로 둔다.
- **토큰**: aelix가 assistant 메시지마다 남긴 API usage의 합. 로컬 Qwen(`openai-completions`): 비캐시 입력 = input − cache_read, 캐시
  입력 = cache_read, 출력 = output, 캐시 쓰기·reasoning은 `not_measured`(usage에 없거나 aelix가 읽지 않음). 프론티어(`openai-codex-responses`,
  `openai-responses`와 같은 규칙): 비캐시 입력 = input, 캐시 입력 = cache_read, reasoning(출력의 일부, `note`: 0은 미보고와 구별되지 않음),
  캐시 쓰기는 `not_measured`(aelix가 0으로 적을 뿐 읽지 않음). usage가
  없는 요청이 있으면 `complete: false`와 `partial`이고, 어떤 요청의 usage에 없는 항목은 0이 아니라 `not_measured`다. 실패한 요청
  (stop reason `error`·`aborted`)에 aelix가 채운 0은 보고로 세지 않는다. `per_request`는 요청마다 턴 번호와 usage, `per_turn`은 사람이 보낸
  요청(턴)마다의 합이고, 제안 조건 `requested`에서는 덧붙인 제안 요청의 턴을 `proposal_request`로 따로 둔다. 전체 입력(`full_input`)은
  `not_measured`다(aelix가 요청 본문을 사건으로 내지 않음). dry-run에서는 가짜 서버가 읽은 요청 본문의 도구 schema·시스템 지침 바이트
  수를 `request_body`에 적고(같은 aelix·표면·checkout의 실제 요청과 도구 schema가 같다. 시스템 지침은 작업 경로처럼 run마다 다른 부분이
  있다), 실제 모델에서는 `not_measured`다. dry-run의 토큰 수는
  스크립트가 정한 값이며 측정이 아니다.
- **프로젝트의 변화**: 새 기록, 새 run(상태·차트와 그 파일·데이터 열·출력 표의 행 수와 열), CONTEXT.md 변경, 실패한 run을 뒤의 run이
  고쳤는지.
- **채점**: `outcome`(`success`·`failure`·`error`·`timeout`·`not_run`), 검사별 결과, 필수 사실 회수율, 근거 검사, 답에 나온 없는 ID
  (`fabricated_ids`), 중대 오판(`critical`), 사람이 확인할 것(`needs_review`).
- **로그인·비밀**: 로그인을 쓰는 모델이면 `model.auth`(출처, provider, 파일 경로, `login_check`: aelix가 나열한 행과 그 에이전트
  디렉터리의 검사 결과)와 `login`(로그인 파일의 메타데이터: run 전 `before`, aelix 종료 때 `after_exit`, 검사 뒤 마지막 `final`.
  수정 시각·inode·이름 수, run의 에이전트 디렉터리에 저장소가 생겼는지 `agent_dir_store`), `login_mentions`(로그인을 가리킨 도구 호출).
  모든 run에 `secret_scan`(위 [프론티어 모델](#프론티어-모델), 로그인의 정확한 값으로 가린 결과는 `secret_scan.exact`), 멈춘 run에는
  `stop`(aelix를 멈춘 방법, 런처가 가림을 기다렸으면 `launcher_waiting`).

## 채점

정답 파일의 `checks`가 규칙이다. `kind`는 `fact`(필수 사실), `evidence`(근거 ID), `uncertainty`(한계·불확실성 표시), `state`(실행 뒤의
프로젝트), `behavior`(도구 사용), `critical`(중대 오판)이다. 성공은 `required` 검사가 모두 통과하고 중대 오판이 없을 때다. 실패한
실행도 분모에 남는다. 자동 검사는 **필요조건**이다: 통과는 정답의 낱말·값·ID·산출물이 있다는 뜻이지 분석이 바르다는 확인이 아니다.
`needs_review`가 있는 run은 사람이 확정한다(#24).

- 답은 문단·줄·문장·절로 나눠 읽는다. 낱말은 대소문자를 무시하고, ASCII 낱말은 다른 낱말 안에서 시작하지 않는다(`correct`는 `incorrect`
  안에 없음). 부정된 낱말(`미해결`, `해결되지 않은`, `not resolved`, `잘린 곳은 없`)과 물은 낱말(`남아 있는지`, `whether`)은 세지 않는다.
  글 검사는 기록 ID·run id를 지우고 본다(`case-overwritten`의 `overwr`는 낱말이 아님).
- 수는 부호를 지닌다(`-20.6%`는 20.6%가 아님). 천 단위 쉼표·(좁은) 띄어쓰기(`5 838`), 지수 표기(`5.838e3`), 한국어 단위(`3,258만 6,900원`,
  `4억 4,596만 9,600`)를 읽는다. 값 바로 뒤의 부정(`5,838개가 아니라`, `is the old wrong count`)은 그 값을 말한 것으로 치지 않는다. 비율
  검사는 `%`가 붙은 수와 소수로 쓴 비율(`0.118`)을 받는다. `number_near`는 값과 그 지표의 낱말을 묶는다: 값을 담은 가장 작은 절·문장
  (·문단)에서 가장 가까운 앞쪽(또는 양쪽) 낱말이 자기 지표의 것이어야 한다(T09: 서울 값과 8월 전체 매출 값을 바꿔 쓰면 둘 다 실패,
  T08: 20.6%에 가장 가까운 방향 낱말이 증가여야 함).
- 중대 오판(`stated_as_current`)은 문장 단위다: 패턴이 부정되지 않고 물음이 아닌 채 나온 문장에, 부정되지 않은 정정·철회·원인 낱말
  (`unless`)이나 그 기록을 닫은 기록 ID(T01의 `resolved_by`, 예: `e56 → e139`)가 없으면 발생한다. 패턴이 나온 문장은 발생하지 않았어도
  모두 `needs_review`다.
- 주장 판정(`claim_verdicts`)은 문장마다 판정 표현을 가장 긴 것부터 낱말 단위로 찾고(`not supported`·`뒷받침되지 않`이 `supported`·
  `뒷받침`보다 먼저, 부정되면 뒷받침↔거짓), 접속어로만 이어진 ID들(`e15와 e20은 거짓`)이 판정을 나눠 가진다. 판정은 앞쪽의 가장 가까운
  ID 묶음에, 판정이 머리에 오면(`Supported: e8`) 뒤의 묶음에 붙는다. 판정이 없는 ID 한 줄(제목)은 다음 ID 줄 전까지의 판정을 받는다.
  한 주장에 판정이 둘 이상이면 실패이고 `needs_review`다. 맨 낱말 `뒷받침`은 홀로 쓰였을 때만(표 칸), 영어 형용사는 관사 뒤가 아닐 때만
  판정이다(`the correct maximum`은 판정이 아님).
- 답에 나온 없는 ID(`fabricated_ids`)는 실패로 치지 않고 `needs_review`로 둔다(휴리스틱이라). T12(자유 분석)는 정답의 `always_review`로
  언제나 `needs_review`다. T12의 상태 검사는 활동 수만이 아니라 결과를 본다: 마지막 성공 run의 그림 하나가 네 센서를 모두 담는지(차트
  spec 파일·데이터의 열과 글 값), 성공한 run이 시점별 점수 표(시점 열과 점수 열, 행 × 점수 열 ≥ 19,200)를 남겼는지(Parquet은 kit이 적은
  행·열, CSV는 파일의 머리와 줄 수).
- `summary.json`의 집계는 성공, 사람이 볼 것이 없는 성공(`success_without_review`), `needs_review`·`fabricated_ids`가 있는 run 수를 따로
  세고, 토큰 중앙값은 모든 요청이 usage를 보고한 run만으로 낸다(`token_runs`, 뺀 run은 `partial_usage_runs`). `summary.md`는 부분 값에
  `(partial)`을 붙이고, `not_run` 행의 칸은 모두 `—`다.
- 모델의 자기평가는 쓰지 않는다. 같은 코드·같은 방법을 요구하지 않는다(T12는 결과물과 기록으로 본다).

## 격리

신뢰 목록, uv(실제 uv로의 링크), aelix의 에이전트 디렉터리(`AELIX_CODING_AGENT_DIR`)와 세션 디렉터리, HOME(run의 `home/`; uv의 캐시와
Python 위치는 `UV_CACHE_DIR`·`UV_PYTHON_INSTALL_DIR`·`XDG_CACHE_HOME`·`XDG_DATA_HOME`으로 넘김)이 모두 `OUT` 안에 있다. 모델이
일하는 작업 루트는 `--work-root`(기본: 시스템 임시 디렉터리) 아래의 새 디렉터리로 `OUT` 밖에 있고 `project/`와 `input/`만 담는다:
`../../`로 올라가도 fixture(정답이 든 `manifest.json`·`expected.json`)와 다른 run이 없다. aelix가 끝나면 작업 루트를 run의 `work/`로 옮긴다.
모델은 bash로 기기의 다른 경로도 읽을 수 있으므로, 작업 루트 밖을 가리키는 호출은 `outside_work_root`, `.aelix`·`auth.json`·로그인
파일을 가리키는 호출은 `names_login`으로 `needs_review`에 남긴다(`~`는 run의 HOME으로 풀리지만 절대 경로는 막지 못함). aelix에는 호출자의
`AELIX_*`·`PI_*`·`LAB_*`(옛 이름 포함)와 이름이 자격 증명을 뜻하는 변수(TOKEN·KEY·SECRET·PASSWORD·PASSWD·AUTH·CREDENTIAL·COOKIE)를 넘기지
않는다(모델 설정의 키 변수만 예외, 지운 이름은 `command.json`의 `stripped_env`). 하네스는 `~/.aelix` 아래 어떤 파일도 열거나 쓰지 않는다:
로그인 파일은 메타데이터(있는지, 수정 시각, inode, 이름 수)만 본다. 프로젝트 파일(기록, run 기록, CONTEXT.md, 차트·표)은 링크를 푼 실제
경로가 프로젝트 안일 때만 읽는다(밖을 가리키는 링크는 `outside_link`). 로그인을 쓰는 모델에서는
aelix 자신이 로그인 파일 하나만 그 자리에서 읽고 갱신하며, 세션·설정·신뢰는 run의 에이전트 디렉터리에 남는다(`~/.aelix/sessions`에
쓰지 않음). aelix는 `--no-session --offline --no-context-files`로 띄운다(작업 디렉터리 위의 AGENTS.md를 읽지 않음).

## 한계

- 정답은 소유자가 확인했지만 글 기반 판정은 휴리스틱이다(필요조건, `needs_review`는 사람이 확정). 정답은 seed 0 fixture에만 있다.
- Q12의 파생·코드만 수정한 재계산·부모 버전 변경은 지표 기록(#14)이 없어 과제가 없다. Q5의 `always_on`은 kit의 설정(#17)이 생기면 실행한다.
- aelix는 요청 본문(시스템 지침·도구 schema)을 사건으로 내지 않는다. 실제 모델 run의 전체 입력 크기는 API의 입력 토큰으로만 알고, 그
  본문의 바이트 수는 같은 조건의 dry-run이 남긴다.
- 실제 모델 실행은 macOS(이 Mac)에서만 했다. dry-run 시험은 uv와 aelix 0.1.0b2를 쓸 수 있는 모든 플랫폼에서 돈다.
- 프론티어 run은 aelix의 진입점을 런처로 감싼다(저장소를 여는 경로 하나와 갱신 중 종료를 미루는 것만 바뀜). 모델은 bash로 기기의 다른
  파일(로그인 파일 포함)을 읽을 수 있다(aelix의 headless 권한 기본값이 허용): 그런 호출은 `outside_work_root`·`names_login`이고, 출력에
  남은 자격 증명 모양은 비밀 검사가 덮어쓰고 실패로 적는다. **모델이 읽은 것은 도구 결과로 이미 provider에 보내졌고, 검사는 그것을
  되돌리지 못한다**(로그인 파일에 든 다른 provider의 자격 증명도 같음).
- 모양 검사는 모양으로 찾는다. 다른 모양의 비밀은 찾지 못할 수 있다: 256자보다 긴 값, 점이 든 값(`v1.x.y`), 소문자 16진수,
  UTF-16으로 쓴 값. 로그인의 값은 런처가 정확한 값으로 가리지만(POSIX), 줄로 나뉘거나 다르게 인코딩된 값, 값의 일부, 로그인에 없는
  비밀(모델이 읽은 다른 파일의 키)은 남을 수 있다. Windows에서는 정확한 값으로 가리지 못해 로그인을 쓰는 run은 `exact_not_run`으로
  실패한다. 하네스와 런처 사이의 파이프는 aelix의 도구에 넘기지 않지만, 같은 사용자의 프로세스가 일부러 끼어드는 것(Linux의
  `/proc/<pid>/fd`)까지 막지는 않는다.
- 하네스를 `kill -9`로 끝내면 그 run은 `scan OUT`을 실행하기 전까지 검사되지 않은 채 남는다(요약에 `not_scanned`). 런처의 lifeline과
  aelix 그룹의 SIGTERM 유예는 POSIX에서만이다. Windows는 aelix의 트리를 바로 끝내고(`taskkill /T /F`), 프로세스 목록에 작업 디렉터리가
  없어 작업 루트의 프로세스는 명령줄과 aelix의 자손으로만 찾으며, Python의 홈과 PowerShell의 `~`는 `USERPROFILE`이라 HOME만 바꾼다.
- 작업 루트를 옮긴 뒤 aelix의 자손이 아닌 프로세스가 작업 디렉터리 밖에서 run에 쓰면 그 쓰기는 검사 뒤에 올 수 있다(검사 중에 바뀐
  파일만 `written_after_scan`으로 잡음).
