# 실제 모델 평가 (Issue #40)

[`tools/model_eval.py`](../model_eval.py)가 실제 모델로 평가 과제를 실행하고 정답 파일로 채점한다. [베타 품질 기준](../../docs/BETA-QUALITY-GATES.md)
§3의 "12개 과제 × 3회 × 두 모델"(Q2·Q3·Q5·Q11·Q12·Q13) 측정을 준비한다. 캠페인 실행과 결과표는 #24의 일이다. 데이터는 #41의
[평가 fixture](../eval_fixtures_data/README.md)(seed 0)만 쓴다.

## 파일

| 파일 | 내용 |
|---|---|
| [`tasks.json`](tasks.json) | 과제 12개: 사용자 요청 글, fixture, 준비 방식, 게이트, Q11 요청 유형, Q5 조건, 제한 시간 |
| [`answers/`](answers/) | 과제별 정답: 바른 완료(글), 근거 ID, 채점 검사. **모두 초안**(`status: draft`) |
| [`models/`](models/) | 모델 설정: [로컬 Qwen](models/qwen3.8-27b-local.json), [프론티어](models/gpt-6-astra.json), [dry-run](models/dry-run.json) |
| [`conditions.json`](conditions.json) | 도구 표면(Q11), 제안 조건(Q5), 글 판정에 쓰는 낱말 묶음 |
| [`dry_run/`](dry_run/) | dry-run의 가짜 모델이 따르는 스크립트(과제별, 없으면 상태 확인 뒤 답 하나) |

정답은 에이전트가 fixture를 읽고 쓴 초안이다. 소유자가 확인하면 `status`를 `confirmed`로, `confirmed_by`에 확인한 사람을 적는다.
초안 정답의 점수는 확정 결과가 아니다(run 기록의 `answer.status`). 정답을 바꾼 뒤에는 `score`로 이미 실행한 run을 다시 채점한다.

## 과제

| ID | fixture | 게이트 | 무엇을 보나 |
|---|---|---|---|
| T01 | S | Q2·Q12 | 새 세션에서 목표·핵심 질문·다음 할 일·열린 질문(닫힌 질문을 열린 것으로 말하지 않음) |
| T02 | S | Q2 | 정정된 발견(e41 → e287)과 철회된 결정(e68 → e274) |
| T03 | S | Q2·Q3 | 최근 목록 밖의 오래된 중요한 발견(e24) |
| T04 | S | Q3 | 맥락 블록에서 빠진 CONTEXT 섹션, 4,000자에서 잘린 기록(e74) |
| T05 | S | Q2·Q3 | 덮어쓴 표: 당시와 지금, 보존되지 않은 내용 |
| T06 | Q10 | Q11·Q3 | 정답 도구가 하나인 요청(영어) |
| T07 | Q10 | Q12·Q5 | 분모가 틀린 전환율: 정의 정정과 기록 |
| T08 | Q10 | Q12·Q5·Q13 | 부분 월 비교: 같은 일수끼리 다시 비교 |
| T09 | Q10 | Q12·Q2 | 두 지표의 값이 둘씩: 집계 키·단위 |
| T10 | Q13 | Q13·Q2 | 기록된 주장 넷을 저장된 수치로 판정(뒷받침·거짓·판단 불가) |
| T11 | Q13 | Q2·Q3 | 실패한 run의 부분 산출물과 지금 바이트의 생산자 |
| T12 | Q13 입력 | Q13·Q11·Q5 | 새 프로젝트에서 모호한 EDA → 스텝 프로파일·점수 → 모든 센서 그림(요청 3개) |

요청 글은 도구 이름을 말하지 않아 도구 표면과 이름 전환 전후(#9)에 같은 글을 쓴다. 모델에게는 fixture의 `project/`와 `input/`만
준다(`manifest.json`·`expected.json`은 정답을 담음).

## 실행

개발 환경에서 실행한다(fixture를 만들려면 분석 라이브러리가 필요하다). aelix는 하네스가 `uv run --isolated --no-project --python 3.12
--with aelix==0.1.0b2 aelix`로 띄운다(`--aelix-command`로 바꿈).

```sh
uv run --locked python tools/model_eval.py validate --fixtures <dir>          # 설정 검사 + 정답의 ID가 fixture에 있는지
uv run --locked python tools/model_eval.py run --model dry-run --task T06 --out <dir>
uv run --locked python tools/model_eval.py serving-check --model qwen3.8-27b-local
uv run --locked python tools/model_eval.py run --model qwen3.8-27b-local --all --repeats 3 --out <dir>
OPENAI_API_KEY=<key> uv run --locked python tools/model_eval.py run --model gpt-6-astra --task T06 --out <dir>
uv run --locked python tools/model_eval.py score <dir>                        # 저장된 run을 지금 정답으로 다시 채점
```

`run`의 선택: `--task`(여러 번) 또는 `--all`, `--repeats N`(반복 번호는 `--first-repeat`부터), `--surface`, `--proposal`,
`--checkout DIR`, `--fixtures DIR`(fixture를 만들고 다시 쓰는 곳, 기본 `OUT/fixtures`), `--seed`, `--timeout`(초, 기본은 과제의 값),
`--script`(dry-run 스크립트). `--out`은 새 디렉터리, 빈 디렉터리, 이전 출력(`summary.json`이 있음) 중 하나다. 이전 출력에는 run을
더하며 같은 run 이름이 있으면 거절한다.

종료 코드: 0(요청한 run을 모두 실행·기록함. 과제 실패와 시간 초과는 데이터), 1(어떤 run을 실행하거나 기록하지 못함), 2(사용법·설정
오류, 아무것도 실행하지 않음), 3(전제가 없어 실행하지 않음: 키 없음, 서빙이 고정 설정과 다름, kit에 없는 조건. `summary.json`에
`not_run`과 이유).

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

[`models/gpt-6-astra.json`](models/gpt-6-astra.json): OpenAI 모델 문서(2026-10-07)가 복잡한 추론·코딩에 먼저 권하는 `gpt-6-astra`(날짜
snapshot 없음, context 1,050,000, 최대 출력 128,000). aelix 0.1.0b2의 내장 `openai` provider(Responses API)로 부르고, 키는 aelix가
`OPENAI_API_KEY`에서 읽는다. 하네스는 키 값을 쓰지 않는다: `command.json`에는 변수 이름만 있고, run이 쓴 파일에 키 값이 나오면
`<redacted>`로 바꾸고 `secret_redacted`를 기록한다. 키가 없으면 실행하지 않고 실행할 명령을 남긴다.

## run 기록

`OUT/runs/<과제>-<모델>-<표면>-<제안 조건>-r<반복>/`에 `run.json`(아래), `events.jsonl`(aelix `--mode json`의 사건 그대로),
`stderr.txt`, `command.json`(argv·cwd·하네스가 정한 변수), `agent/`(aelix의 상태와 `models.json`), `work/`(모델이 일한 프로젝트와 입력)이
남는다. `OUT/summary.json`과 그것에서 만든 `summary.md`가 run과 (모델, 과제, 표면, 조건)별 집계를 담는다.

- **모델·서빙**: 설정 id, provider, model, API, thinking, backend와 버전, 양자화, digest, tokenizer, context, 출력 예약, 표본 추출, 서빙 확인
  결과, 실제로 답한 모델(`observed_models`).
- **호스트·kit**: aelix 버전, Python·플랫폼, checkout 경로·commit·dirty, kit 버전, 확장 파일 sha256, 이름 세대.
- **조건**: 과제, 반복 번호, seed, fixture(종류·생성기 버전·digest), 표면, 제안 조건, 정답 상태, 제공된 도구 목록(dry-run에서 관측, 그 밖엔
  `--tools` 또는 aelix 기본).
- **실행**: 시작·끝·걸린 시간, 종료 코드, 시간 초과, 요청과 답(턴마다), 모델 요청 수, 자동 재시도, 압축.
- **도구 호출**: 이름·역할·인자·결과 앞부분·`error_class`. 인자 오류는 `unknown_tool`(없는 도구), `schema`(aelix의 인자 검증 실패),
  `argument`(확장이 인자 때문에 거절한 오류 코드)의 합이다. `query_error`(SQL 실행 오류), `tool_error`, `run_failed`(스크립트가 실패한
  분석 run)는 따로 센다. bash로 Python을 돌린 호출(`bash_python`)도 센다.
- **토큰**: aelix가 assistant 메시지마다 남긴 API usage의 합. 로컬 Qwen(`openai-completions`): 비캐시 입력 = input − cache_read, 캐시
  입력 = cache_read, 출력 = output, 캐시 쓰기·reasoning은 `not_measured`(usage에 없거나 aelix가 읽지 않음). 프론티어(`openai-responses`):
  비캐시 입력 = input, 캐시 입력 = cache_read, reasoning(출력의 일부), 캐시 쓰기는 `not_measured`(aelix가 0으로 적을 뿐 읽지 않음). usage가
  없는 요청이 있으면 `complete: false`와 `partial`. dry-run의 수는 스크립트가 정한 값이며 측정이 아니다.
- **프로젝트의 변화**: 새 기록, 새 run(상태·차트), CONTEXT.md 변경, 실패한 run을 뒤의 run이 고쳤는지.
- **채점**: `outcome`(`success`·`failure`·`error`·`timeout`·`not_run`), 검사별 결과, 필수 사실 회수율, 근거 검사, 답에 나온 없는 ID
  (`fabricated_ids`), 중대 오판(`critical`), 사람이 확인할 것(`needs_review`).

## 채점

정답 파일의 `checks`가 규칙이다. `kind`는 `fact`(필수 사실), `evidence`(근거 ID), `uncertainty`(한계·불확실성 표시), `state`(실행 뒤의
프로젝트), `behavior`(도구 사용), `critical`(중대 오판)이다. 성공은 `required` 검사가 모두 통과하고 중대 오판이 없을 때다. 실패한
실행도 분모에 남는다.

- 글 검사는 대소문자를 무시한 부분 문자열, 수는 천 단위 쉼표를 지운 수(허용 오차), 비율은 `%`가 붙은 수다. 기록 ID·run id는 ASCII 경계로
  찾는다(`e41의`는 e41, `e410`은 아님).
- 중대 오판(`stated_as_current`)은 패턴이 있는 줄에 정정·철회·부정이나 그 값의 원인을 말하는 낱말이 없으면 발생한다. 주장 판정
  (`claim_verdicts`)은 각 ID가 나온 구간의 판정 표현을 본다. 둘 다 휴리스틱이라 발생하거나 애매한 것은 `needs_review`이고, 확정은
  사람(#24)이 한다.
- 모델의 자기평가는 쓰지 않는다. 같은 코드·같은 방법을 요구하지 않는다(T12는 결과물과 기록으로 본다).

## 격리

신뢰 목록, uv(실제 uv로의 링크), aelix의 에이전트 디렉터리(`AELIX_CODING_AGENT_DIR`)와 세션 디렉터리가 모두 `OUT` 안에 있다. 옛 이름의
변수(`AELIX_LAB_*`·`LAB_*`)와 호출자의 `AELIX_ANALYTICS_*`는 지운다. `~/.aelix`를 읽거나 쓰지 않는다. aelix는 `--no-session --offline
--no-context-files`로 띄운다(작업 디렉터리 위의 AGENTS.md를 읽지 않음).

## 한계

- 정답은 초안이다. 글 기반 판정은 휴리스틱이다.
- Q12의 파생·코드만 수정한 재계산·부모 버전 변경은 지표 기록(#14)이 없어 과제가 없다. Q5의 `always_on`은 kit의 설정(#17)이 생기면 실행한다.
- aelix는 요청 본문(시스템 지침·도구 schema)을 사건으로 내지 않는다. 전체 입력의 크기는 API의 입력 토큰으로만 안다.
- 실제 모델 실행은 macOS(이 Mac)에서만 했다. dry-run 시험은 uv와 aelix 0.1.0b2를 쓸 수 있는 모든 플랫폼에서 돈다.
