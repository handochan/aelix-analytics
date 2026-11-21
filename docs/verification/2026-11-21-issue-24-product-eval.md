# Issue #24 — 출시 후보 0.32.1, 확장이 있을 때와 없을 때(E4) (2026-11-21)

[Issue #24](https://github.com/handochan/aelix-analytics/issues/24)의 0.32.1 후보로 다시 잰 모델 측정 기록이다([릴리스 절차](../../CONTRIBUTING.md#릴리스-절차) 3단계).
소유자가 전체 캠페인을 다시 돌리기로 정했다(2026-11-20, #24 코멘트 7042401591). 방법은 0.32.0의 [E4 기록](2026-11-04-issue-24-product-eval.md)과
[제품 효과 평가 설계](../design/PRODUCT-EVALUATION.md)대로다. 같은 과제를 aelix-analytics가 있을 때(A)와 aelix만(V, 확장·kit 없음)으로 두 모델에 돌렸다.
수치·해시의 정본은 [JSON](2026-11-21-issue-24-product-eval.json)이다. 원자료(run 276개)는 이 기기의 run 디렉터리에 두고 커밋하지 않았다.

## 결과

두 모델 모두 규칙대로 모델 게이트(설계 8절)를 통과했다. 소유자의 별도 판정(O9·O11)이 필요하지 않다. 지원 하단의 로컬 Qwen은 `not_run`이다(O10).

| Gate | 결과 | 수행자 | 환경 | 제한 |
|---|---|---|---|---|
| 모델 게이트 gpt-6.1-sol(상위) | pass | 에이전트가 실행·채점, 소유자가 정답과 맥락 오류를 확정 | aelix 0.1.0b2, macOS 26.6.2 arm64 | 3회 반복, fixture seed 0 하나 |
| 모델 게이트 Qwen3.8 27B(하위) | pass | 같음 | 같음, OpenRouter(서빙 미고정) | 같음 |
| Q2 분석 연속성(R1·R3·R4) | pass(두 모델) | 같음 | 같음 | 같음 |
| Q3 토큰(R6) | 보고 | 같음 | 같음 | 아래 R6 |
| Q12 지표와 분석 방향(C3) | 새 세션의 회수 gpt A 9/9 · V 7/9, Qwen A 7/9 · V 5/9 | 같음 | 같음 | 정의 변경 하나만 잼 |
| Q13 분석 자유도(R5) | pass(두 모델) | 같음 | 같음 | — |
| Q5 재사용 제안·Q11 도구 표면 | 이 표 밖 | — | — | 같은 하네스로 이어 돌림([2026-11-21-issue-24-q5-q11](2026-11-21-issue-24-q5-q11.md)) |

## 규칙별 결과

비율은 규칙의 범위에 든 run(반복 3회)의 검사를 합산한 값이다. "A:V"는 답 성공 run 수다.

### gpt-6.1-sol(상위)

| 규칙 | 판정 | 값 |
|---|---|---|
| R1 재개 | pass | 재개 정확도 A 1.00 · V 1.00(C1–C3, T01–T03, T14). 두 조건 모두 만점이라 차이 없음(O6) |
| R2 근거·한계 | pass | 근거 A 1.00 · V 0.83, 한계 A 1.00 · V 0.50, 없는 ID A 0 · V 0 |
| R3 맥락 오류 | pass | 사람이 확정한 맥락 오류 A 0 · V 0 |
| R4 축적 | pass | 회수율 A 1.00 · V 0.91(짝 23, 해당 없음으로 뺀 짝 7). 중복 분석 A 0 · V 4. V가 만점이 아니라 O9의 천장이 아니다 |
| R5 해 없음 | pass | T06 3:3, T07 1:0, T08 0:0, T09 3:3, T10 0:0, T12 0:0, 체인 세션 1 C1 2:1, C2 3:3, C3 3:3 |
| R6 비용 | 보고 | 1.2배를 넘은 과제(답 성공 run의 토큰 중앙값 A · V): C1 336k · 270k, C3 136k · 45k, T09 55k · 28k, T11 62k · 39k |
| R7 절대 하한 | pass | A의 재개 1.00, 회수 1.00(하한 2/3) |

### Qwen3.8 27B(하위, OpenRouter)

| 규칙 | 판정 | 값 |
|---|---|---|
| R1 재개 | pass | 재개 정확도 A 1.00 · V 1.00(차이 없음) |
| R2 근거·한계 | pass | 근거 A 1.00 · V 0.96, 한계 A 1.00 · V 1.00(T04 A 3/3 · V 3/3), 없는 ID A 0 · V 0 |
| R3 맥락 오류 | pass | 사람이 확정한 맥락 오류 A 0 · V 2(C2 V 반복 2 세션 3: 부분 월의 −22.0%를 계획에 쓸 변화로 제시. T01 V 반복 3: 닫힌 질문을 열린 질문으로 나열) |
| R4 축적 | pass | 회수율 A 0.91 · V 0.68(짝 22, 뺀 짝 8). 중복 분석 A 1 · V 11 |
| R5 해 없음 | pass | T06 3:3, T07 3:1, T08 1:1, T09 2:1, T10 0:0, T12 0:1, 체인 세션 1 C1 3:3, C2 3:2, C3 3:3 |
| R6 비용 | 보고 | C1 1,514k · 1,121k, C2 661k · 284k, T05 118k · 85k, T08 162k · 69k, T11 54k · 41k |
| R7 절대 하한 | 보고 | A의 재개 0.98, 회수 0.85 |

R4의 "해당 없음"은 앞 세션이 그 값을 내지 못한 회수 검사다. 뺀 짝은 gpt 7/30, Qwen 8/30으로 절반을 넘지 않아 R4를 판정했다.

### 0.32.0의 O11이 겨냥한 지표

소유자는 0.32.1에서 O11을 다시 적용할 조건을 정했다(#24 코멘트 7042401591). Qwen의 R2가 통과했으므로 그 조건을 볼 일이 없었다.
0.32.0에서 R2를 떨어뜨린 두 원인은 모두 사라졌다.

| 지표 | 0.32.0 | 0.32.1 |
|---|---|---|
| Qwen T04의 한계(글자 수, [#156](https://github.com/handochan/aelix-analytics/issues/156)) | A 2/3 · V 3/3 | A 3/3 · V 3/3 |
| Qwen의 없는 ID(줄여 쓴 run ID, [#158](https://github.com/handochan/aelix-analytics/issues/158)) | A 3 · V 1 | A 0 · V 0 |

## 무엇을 돌렸나

- **후보**: main `494d1ce`(#208)의 빌드다. release run [44753895153](https://github.com/handochan/aelix-analytics/actions/runs/44753895153)이 만들었고
  `docs/releases/0.32.1.sha256`(#213, `84433ab`)이 기록한다. wheel `878741132f704146…`, sdist `2f6f14863467…`.
- **조건**: A는 aelix + aelix-analytics(기본 표면, 제안 없음), V는 aelix만이다. 두 조건이 같은 요청 글과 같은 fixture를 받는다.
- **과제**: Part I 14과제(T01–T14) × 3회 × 2조건 = 모델마다 84 run. Part II 체인 3개(C1–C3) × 세션 3 × 3회 × 2조건 = 모델마다 54 세션.
- **순서**: A와 V를 과제마다 번갈아 돌리고, 먼저 도는 쪽을 반복마다 바꿨다(O8). 두 모델을 병렬로 돌렸다. 2026-11-21 00:27 KST에 시작해
  Qwen은 03:23, gpt는 04:55(다시 돈 run 포함)에 끝났다.
- **kit**: 0.32.1, 확장 sha256 `19f578a7…`. 하네스 checkout은 clean detached `84433ab`다. `494d1ce`와는 `docs/releases/0.32.1.sha256`만 다르므로
  `src/`·`extension/`이 후보와 같다. aelix 0.1.0b2, Python 3.12.13, macOS 26.6.2 arm64다.
- **fixture**: 17과제 모두를 dry run으로 한 번 만들고(후보 kit) 모든 run이 다시 썼다.
- **모델**: gpt-6.1-sol(aelix 로그인), `qwen/qwen3.8-27b`(OpenRouter, 업스트림 공급자는 요청마다 OpenRouter가 고름).
- **다시 돈 run**: 276 run 모두 실행됐고, 비교에 쓴 run에는 `error`·`not_run`이 없다. 다시 돈 run이 같은 이름의 run 17개를 대신한다.
  - gpt 11개: 백엔드가 `Error Code None: None`으로 끝낸 run 5개(T10 A·V 반복 2, C1·C2 A와 C3 V의 반복 2 세션 하나씩)를 다시 돌렸다.
    체인은 세 세션을 모두 다시 돌았다.
  - Qwen 6개: OpenRouter 크레딧이 02:08에 떨어져(402) 끝나지 못한 C1 A·C2 V의 반복 2다. 소유자가 충전한 뒤 02:28부터 남은 run을 이어 돌았다.
- **비밀 검사**: gpt 137/138, Qwen 133/138 통과다. 로그인의 값(9개, 한 run은 12개)과 바이트 단위로 같은 글은 276 run 모두 0건이다.
  - gpt의 실패 1건은 aelix가 끝난 뒤 남은 프로세스(`writers_left`, C3 A)다.
  - Qwen의 실패 5건:
    - 긴 토큰 모양(`long_token` 등) 3건(C3 A). 모델의 thinking이 긴 커서 문자열을 인용한 것이다.
    - `writers_left` 1건(T10 A).
    - `names_login` 1건(T08 V 반복 3). 그 run의 bash 명령이 하네스 `.venv`에서 `__editable__.aelix*.pth`를 찾았고, 그 이름의 `.aelix`가
      로그인 위치를 부르는 호출로 걸렸다(아래 "작업 루트 밖"). 로그인의 값과 같은 글은 없다.
- **채점**: [#214](https://github.com/handochan/aelix-analytics/pull/214)의 head `d5240a7`에서 모든 run을 다시 채점했다. 그 정답 파일은 병합 뒤에도 같다.
  - #214는 맞는 답을 놓친 네 검사를 넓혔다(T01 `next-step`, T02 `same-period`, T14 `current-value`·`no-run`, 소유자 승인 2026-11-21).
  - 다시 채점해 통과로 바뀐 검사는 9개다. gpt 5개(T01 V 1, T02 A 3 · V 1)이고 그 run 5개는 모두 성공으로 바뀌었다. Qwen 4개(T14 A 3 · V 1)는
    run의 결과를 바꾸지 않았다(중대 오판 검사가 남음. 아래 확정에서 오판으로 확정). 통과에서 떨어진 검사는 0이다.
  - 다시 채점 전의 기록은 run 디렉터리에 보관했다.
- **첫 채점을 둔 run 1개**: Qwen C3 A 반복 2 세션 3이다. 비밀 검사가 첫 채점 뒤에 그 `events.jsonl`의 긴 토큰을 같은 길이의 표시로 덮어썼다.
  그 가운데 하나가 역슬래시 바로 뒤에서 시작해 172줄이 JSON으로 읽히지 않게 됐다. 다시 채점하면 하네스가 그 run을 끝나지 않은 것(`error`)으로 읽는다.
  C3의 검사는 #214가 바꾼 넷에 들지 않으므로 첫 채점을 그대로 두었다. 다시 채점한 사본은 run 디렉터리의 evidence에 있다. 다른 run에는 이런 일이 없었다
  (다시 채점 전후에 읽지 못한 줄의 수가 바뀐 run은 이것 하나다).
- **맥락 오류**: 중대 오판 검사가 걸린 39건(38 run)을 사람이 확정했다(소유자 2026-11-21, 에이전트의 제안대로). 실제 2건은 둘 다 V다(위 R3).
  나머지 37건은 옛 값을 부정하거나, 출처로 설명하거나, 맞는 값과 견준 문장이다. 확정 파일은 [confirmations](2026-11-21-issue-24-product-eval-confirmations.json)이고
  해시는 JSON에 있다. 비교 도구가 확정 뒤 바뀐 run(낡은 확정)을 0건으로 보고했다.
- **비교**: `tools/model_eval_compare.py`(`d5240a7`)를 두 모델의 출력, 캠페인의 fixture(`--fixtures`)와 확정 파일로 돌렸다.

## 작업 루트 밖

run의 도구 호출이 작업 루트 밖의 하네스 checkout을 쓴 일이 있었다. Qwen만이고 gpt는 0이다.

- fixture를 만든 dry run이 하네스의 `.venv`에서 돌았다. 그래서 fixture 프로젝트의 run 기록(`env.executable`)에 그 python의 절대 경로가 남았다.
  모델이 그 기록을 읽고 경로를 따라갔다.
- 그 python(분석 라이브러리가 있음)으로 분석 코드를 돌린 run: A 2(T05 반복 2·3), V 5(T05 반복 1, T08 반복 1, T09 반복 2, T10 반복 3, T13 반복 1).
- 하네스 checkout의 kit 소스를 읽은 run: A 2(C1 반복 1 세션 2, C2 반복 3 세션 1), V 2(T08 반복 1·3).
  - V의 두 run은 그 python으로 `aelix_analytics`를 import하려 했지만 실패했다(`ModuleNotFoundError`). 그 `.venv`에는 kit이 설치되어 있지 않다.
  - 두 run은 kit의 API(`record`·`read` 등)를 소스로 읽었다. kit을 실행하지는 못했다.
- V의 C2 반복 1 세션 3은 캠페인 출력의 run 목록을 `ls`했다. 다른 run의 파일은 읽지 않았다.
- 판정에 주는 영향: 이 일들은 V가 순정보다 더 가진 쪽으로만 기운다. 그래서 A ≥ V를 요구하는 R1·R2·R4와 A ≤ V를 요구하는 R3의 통과를 바꾸지 않는다.
  0.32.0의 캠페인에서는 A run 2개만 하네스 checkout을 썼다.

## 진단과 개선 후보

설계 7절의 진단이다. 0.32.0에서 Issue로 고른 후보 가운데 #156·#158은 0.32.1에서 고쳤고(위 표), #152–#155는 열려 있다.

| 부품 | 보인 것 | 0.32.0 | 관련 |
|---|---|---|---|
| 이어 가기의 비용(상위 모델) | gpt 체인의 토큰 중앙값 C1 1.2배, C3 3.0배(R6). 회수는 A 1.00 · V 0.91 | C1 1.6배, C3 2.6배, 회수 A·V 1.00 | [#152](https://github.com/handochan/aelix-analytics/issues/152) |
| 원시 파일 읽기 | kit의 원시 파일(journal, run 기록)을 직접 읽은 A run: gpt 26/69, Qwen 26/69 | 두 모델 합 67/138 | [#153](https://github.com/handochan/aelix-analytics/issues/153) |
| 도구의 인자·형식(하위 모델) | Qwen A의 도구 오류 125, 하네스가 인자 오류로 분류한 것 19(V 64 · 0) | A 175 · 7, V 101 | [#154](https://github.com/handochan/aelix-analytics/issues/154) |
| 새 프로젝트의 비용(하위 모델) | Qwen C1 세션 1의 토큰 중앙값 A 839k · V 210k, 분석 실행 53 · 34 | A 853k · V 88k, 73 · 39 | [#155](https://github.com/handochan/aelix-analytics/issues/155) |
| 받은 값을 답에 쓰기 | 두 모델의 A run에서 run이 받은 근거 ID를 답이 쓰지 않음: T07 1, T08 9(`cites`)·6(`cites-sound`), T10 6 | T08 3, T10 6 | 보류(0.32.0 처분) |
| 근거가 남는가 | gpt V는 C2 세션 3에서 근거 0/3, A 3/3. Qwen은 V 2/3, A 3/3 | gpt 0/3 · Qwen 1/3(V) | 보고(확장의 강점) |
| 하네스의 격리 | 위 "작업 루트 밖"(fixture의 실행 파일 경로) | A 2 run | 후속 제안(하네스, 배포본 밖) |
| 하네스의 다시 채점 | 비밀 검사의 표시가 JSON의 escape를 깨면 다시 채점이 그 run을 `error`로 읽음(위) | 보지 못함 | 후속 제안(하네스, 배포본 밖) |

## 측정하지 않은 것

- 지원 하단의 로컬 Qwen(D2): `not_run`(O10).
- 메모 규약이 있는 순정(O2)과 사람의 비교(E7, O4): 하지 않기로 함.
- Linux·Windows의 aelix, 다른 fixture seed, 반복 3회를 넘는 분산, OpenRouter의 업스트림 공급자 고정.
- 답에 섞인 다른 문자(0.32.0은 보고했다)는 이번에 세지 않았다.
- 중대 오판 검사(`stated_as_current`)는 제목을 보지 않는다. 제목 아래의 옛 값은 걸려서 사람이 확정한다.

## 명령

```sh
# 하네스: clean detached 84433ab. AELIX_ANALYTICS_TRUST_FILE은 임시 파일
uv run --locked python tools/model_eval.py run --model <모델> --out <out> --fixtures <fixtures> --task <과제> --surface <default|vanilla> --first-repeat <r> --repeats 1
# 채점·비교: clean detached d5240a7
uv run --locked python tools/model_eval.py score <out>
uv run --locked python tools/model_eval_compare.py <out>... --out <compare> --fixtures <fixtures> --confirmations <확정 파일>
```
