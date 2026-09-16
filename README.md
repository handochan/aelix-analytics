# aelix-analytics-baseline

aelix의 **기본 확장 계약만으로** 만든 분석 확장입니다. [aelix-analytics](../aelix-analytics/README.md)를
평가하기 위한 비교 기준선(baseline) MVP입니다.

자유로운 Python 분석(DuckDB·Polars·matplotlib·plotly)에 **가벼운 기록 규약**만 얹었을 때 어디까지 되는지
직접 써 보면서, aelix-analytics의 무거운 계약(원장·ViewSpec·SelectionRef·verify·recover) 중 무엇이 실제로
필요한지 가려내는 것이 목적입니다.

- 분석은 HTML 리포트로 봅니다. 모든 차트에서 **그 차트를 만든 코드(실행된 바이트 그대로, 줄 강조)**,
  **입력 데이터(sha256, 지금 파일과 비교)**, **플롯된 데이터(parquet + 미리보기)** 로 바로 이동합니다.
- 데이터는 Parquet, 처리는 DuckDB·Polars입니다.
- 프로젝트 맥락(CONTEXT.md, 버전 기록), 중요 정보(발견·결정·가정·열린 질문), 시간순 이력(journal)을
  파일로 남기고, 리포트와 에이전트 프롬프트에 함께 보여 줍니다.

> 샌드박스가 아닙니다. aelix는 확장 도구를 실행하기 전에 승인을 묻지 않으므로 `lab_run`은 확인 없이
> 코드를 실행합니다. 실데이터에는 이 점을 알고 쓰세요. [한계](#한계)를 먼저 읽으세요.

## 5분 시작

```sh
PACK=/Users/handochan/dev/aelix-extensions/aelix-analytics-baseline

# 1) 모델 없이 결과물 모양 보기: 합성 데이터 + 예제 run 3개 + 메모 + 재실행 검증
$PACK/bin/lab demo ~/analyses/lab-demo          # 처음엔 uv 환경 준비로 1분 안팎
open ~/analyses/lab-demo/report/index.html

# 2) 그 프로젝트에서 aelix로 이어서 분석
cd ~/analyses/lab-demo
aelix -e $PACK/extension/aelix_lab.py
```

aelix 안에서: `/lab`(현황) · `/lab open`(리포트) · "지역별 성장률 차트에 플롯 데이터도 기록되게 고쳐줘" 같은 요청.

필요한 것: macOS/Linux, [uv](https://docs.astral.sh/uv/)(PATH 또는 `AELIX_LAB_UV`), aelix 0.1.0b2 이상.
분석 라이브러리는 **프로젝트마다 자기 uv 환경**에 설치되며 aelix 환경은 건드리지 않습니다.

## 확장 등록

| 방법 | 명령 | 언제 |
|---|---|---|
| 세션 단위 | `aelix -e $PACK/extension/aelix_lab.py [--lab-project DIR]` | 시험·비교할 때 |
| 전역 등록 | `mkdir -p ~/.aelix/agent/extensions && ln -s $PACK/extension/aelix_lab.py ~/.aelix/agent/extensions/aelix_lab.py` | 계속 쓸 때 (모든 aelix 세션에 도구 6개가 붙음) |
| 해제 | `rm ~/.aelix/agent/extensions/aelix_lab.py` | |

- **복사하지 말고 symlink** 하세요. 확장 파일이 옆의 `kit/`을 찾습니다. 복사했다면 `AELIX_LAB_KIT=$PACK/kit`.
- `aelix status`로 로드 여부를 확인할 수 있습니다.
- **디렉터리로 자동 활성화되는 프로젝트는 "내가 만들었거나 고른 것"뿐입니다.** `lab init`·`lab demo`·`/lab use`가
  프로젝트 경로를 `~/.aelix/agent/lab-projects.json`에 적고, 그 목록에 있는 프로젝트 안에서 aelix를 실행할 때만
  활성화됩니다. 남에게 받은 사본은 `/lab use DIR` 한 번이 필요합니다(그 CONTEXT.md가 프롬프트에 들어가고
  `lab_run`이 그 코드를 실행하기 때문입니다).
- aelix-analytics 어댑터와 이름이 겹치지 않습니다(`lab_*`·`/lab` vs `analysis_*`·`/analysis`).
  다만 비교 평가는 한 번에 하나만 로드해야 공정합니다.

프로젝트는 `/lab use` → `--lab-project` → `AELIX_LAB_PROJECT` → 작업 디렉터리와 그 조상의 `lab.json` 순서로 찾습니다.
가장 쉬운 방법은 프로젝트 디렉터리에서 aelix를 실행하는 것입니다.

## aelix에서의 흐름

1. 프로젝트 만들기: `/lab init ~/analyses/q3-sales 3분기 매출` — 또는 모델에게 위치를 알려 주고 만들어 달라고 합니다(`lab_init`).
2. `CONTEXT.md`에 목표·핵심 질문·데이터 출처·정의를 채웁니다(사람이 직접 또는 모델에게 요청).
3. "이 CSV를 가져와서 월별 추세를 봐줘" → 모델이 `lab_import` → `lab_query`로 탐색 → `scripts/01_*.py` 작성 → `lab_run`.
4. `/lab open`으로 리포트를 열어 둡니다. run·import·메모가 생기면 **자동 새로고침**됩니다.
5. 확정된 내용은 `lab_note`(finding·decision·assumption·question·todo), 막히거나 우회가 필요했던 점은 `/lab gap …`.

### 모델 도구

| 도구 | 하는 일 | 쓰기 |
|---|---|---|
| `lab_status` | 데이터셋·view 이름, 최근 run, 차트 최신 버전과 stale 여부, 열린 질문, 핵심 정보, 최근 journal | 없음 |
| `lab_query` | 데이터셋 view 위의 읽기 전용 DuckDB SQL 한 문장(SELECT/WITH/DESCRIBE/SUMMARIZE/EXPLAIN). 쓰기 문장과 `EXPLAIN ANALYZE`는 거절하고, 파일 접근을 `data/`로 제한합니다 | `.lab/queries.jsonl` 로그만 |
| `lab_import` | CSV/TSV/JSON/NDJSON/Parquet → `data/raw/<name>.parquet`(불변), 원본 경로·sha256·스키마 기록 | 예 |
| `lab_run` | 스크립트 실행 + 코드 스냅샷·입력 해시·출력·차트·플롯 데이터·로그·환경 기록, 리포트 재생성. `rerun_of`로 재실행 비교 | 예 |
| `lab_note` | journal 기록. `resolves`로 이전 질문·할 일을 닫음 | 예 |
| `lab_init` | 새 프로젝트 + uv 환경 (사용자가 요청했을 때만) | 예 |

PNG 차트는 모델이 이미지 입력을 받을 때 `lab_run` 결과에 이미지로도 붙습니다(모델이 자기 차트를 봅니다).

### 사람 명령

```text
/lab                      현황          /lab report | open        리포트 재생성 | 브라우저로 열기
/lab where                프로젝트·kit·uv  /lab note KIND TEXT       사람 이름으로 journal 기록
/lab init DIR [제목]      새 프로젝트     /lab gap TEXT             baseline에서 부족했던 점 기록
/lab demo DIR             데모 프로젝트   /lab context              프롬프트에 주입되는 맥락 블록 보기
/lab use DIR              활성 프로젝트 지정
```

셸에서는 같은 기능을 `bin/lab`으로 씁니다(`lab status`, `lab run scripts/x.py --title …`, `lab query "SQL"` 등).

### 스크립트 API

```python
import labkit as lab

sales = lab.read("sales")                                   # Polars DataFrame (data/raw/sales.parquet)
monthly = lab.sql("SELECT ... FROM sales GROUP BY ALL").pl() # 모든 데이터셋이 view인 DuckDB
lab.save(monthly, "monthly_revenue")                        # data/derived/monthly_revenue.parquet
lab.chart(fig, "monthly-revenue", title="…", caption="…", data=monthly)   # matplotlib/plotly
lab.note("finding", "…")                                    # 이 run에 연결된 journal 기록
```

labkit을 쓰지 않은 **관용 코드도 기록됩니다**: `pl.read_parquet("data/…")`, `duckdb.sql("… FROM 'data/….parquet'")`,
`plt.show()`, plotly `fig.show()`. 다만 **정확히 무엇을 그렸는지(플롯 데이터)** 는 `lab.chart(..., data=df)`만 남깁니다.
matplotlib에는 한글 폰트와 검증된 기본 팔레트가 적용됩니다(`LAB_STYLE=0`으로 끔).

## 프로젝트 구조

```text
my-analysis/
  lab.json                 프로젝트 정체성 (lab.project/1)
  CONTEXT.md               지금 유효한 맥락: 목표·질문·데이터 출처·정의·가정·결론·다음 할 일
  journal.jsonl            append-only 타임라인 (lab.event/1): project·import·run·context·finding·decision·assumption·question·todo·note·gap·resolve
  pyproject.toml, uv.lock  이 프로젝트만의 환경 (DuckDB·Polars·PyArrow·matplotlib·plotly)
  data/raw/<name>.parquet  가져온 데이터(불변 규약) + <name>.meta.json (원본 경로·sha256·스키마)
  data/derived/            run이 만든 Parquet
  scripts/                 편집하는 분석 코드
  runs/<YYYYMMDD-HHMMSS-script>/
    run.json               기록 (lab.run/1): 코드 sha·입력 sha·출력·차트·오류·환경·재실행 비교
    code/                  실행된 코드 스냅샷 (+ import된 로컬 모듈)
    charts/                차트 파일 + <id>.data.parquet (플롯 데이터)
    stdout.txt, stderr.txt
  report/                  생성된 HTML (index·runs/·data/·context.html) — 직접 편집하지 않음
  .lab/                    context 버전, telemetry, query 로그, 캐시
```

필드 정의는 [docs/CONTRACTS.md](docs/CONTRACTS.md)에 있습니다.

## 차트에서 데이터·코드까지

- **기록 단위는 run입니다.** 차트는 반드시 한 run에 속하고, run은 실행한 바이트 그대로의 코드 스냅샷을 가집니다.
  나중에 스크립트를 고쳐도 차트가 가리키는 코드는 바뀌지 않습니다.
- **입력 감지(최선 노력)**: 런타임 훅(Polars 읽기, DuckDB 읽기·SQL 파일 리터럴, `lab.read/scan/sql`),
  코드의 경로 리터럴 정적 스캔, 호출 시 선언(`inputs`), 이 셋을 합칩니다. 각 입력에 어느 경로로 감지됐는지(`via`)가 남습니다.
  **출력**은 run 전후 프로젝트 파일 비교로 빠짐없이 잡습니다.
- **stale**: 차트를 만든 run의 입력 파일 sha256이 지금과 다르거나, 그 입력을 만든 상위 run이 stale이면(전이) 표시됩니다.
- **버전**: `스크립트::차트id`마다 최신 성공 run이 현재 버전이고, 이전 버전은 카드와 run 페이지에서 따라갈 수 있습니다.
- **재실행 검증**: `lab_run(rerun_of=…)`은 원래 스냅샷을 다시 실행해 플롯 데이터 프레임 동등성, 출력 바이트를 비교하고
  `match / mismatch / failed / inconclusive`를 기록합니다. 재실행이 실패했거나, 차트·출력이 사라지거나 새로 생기면
  `match`가 되지 않습니다. 재실행 run은 검증 기록이라 차트의 현재 버전이 되지 않습니다.

## 맥락 · 중요 정보 · 시간 흐름

- `CONTEXT.md`: 사람과 모델이 함께 고치는 현재 맥락. 바뀔 때마다 `.lab/context/`에 버전이 남고 journal에 `context` 이벤트가
  생기며, 리포트의 맥락 기록 페이지에서 diff로 봅니다(run·import·note·프롬프트 시작·에이전트 턴 종료 때 감지).
- `journal.jsonl`: 모든 사건의 시간순 기록. 발견·결정·가정은 **핵심 정보**, 질문·할 일은 **열린 항목**이며,
  나중 기록의 `resolves`로 닫힙니다. run·이벤트에는 aelix 세션 파일 경로가 함께 남습니다.
- **프롬프트 주입**: 매 요청 전에 `CONTEXT.md` + 해결되지 않은 핵심 정보 + 열린 항목을 시스템 프롬프트 뒤에 붙입니다.
  자주 바뀌지 않는 내용만 넣어 프롬프트 캐시를 덜 깨고, 자주 바뀌는 현황은 `lab_status`로 봅니다.

## 평가에 쓰기

- 쓰다가 부족한 점은 바로 `/lab gap <내용>`. 모델도 막히면 `lab_note(kind="gap")`을 남기도록 지침이 들어 있습니다.
- 확장이 **모든 도구 호출**을 `.lab/telemetry.jsonl`에 남기고, 규약 우회 의심(`python_via_bash`, `wrote_managed_path`)을 표시합니다.
- 리포트 **평가** 탭: gap 목록, 기록 품질 신호(입력 미감지 run, 플롯 데이터 없는 차트, 프로젝트 밖 입력, stale 차트, 재실행 결과),
  도구별 호출·오류·시간, 우회 의심 호출.
- 비교 축과 시나리오: [docs/EVALUATION.md](docs/EVALUATION.md).

## 한계

- **샌드박스·승인 없음.** 스크립트는 사용자 권한으로 돌고, aelix는 확장 도구 실행 전에 묻지 않습니다(헤드리스 모드도 자동 승인).
- **계보는 최선 노력.** 실행 중 계산된 경로를 C 확장이 직접 여는 경우, 훅이 없는 API(예: pyarrow 직접 읽기)는 놓칠 수 있습니다.
  경고(`no input files were detected`)를 보고 `inputs`로 선언하세요. 행 단위 계보는 없습니다.
- **불변성은 규약입니다.** `data/raw`·`runs`를 사람이 고치는 것을 막지 않습니다. 해시 비교로 드러날 뿐입니다. 해시 체인·서명 없음.
- **트랜잭션·복구 없음.** 강제 종료된 run은 `incomplete`로 보입니다. 같은 요청을 두 번 보내면 run이 두 개 생깁니다(멱등 키 없음).
  한 프로젝트의 run은 파일 잠금으로 직렬화되므로(출력은 실행 전후 파일 비교로 찾습니다) 동시 요청은 순서를 기다립니다.
  기록 중 일부 단계가 실패하면 run.json의 `finalize_errors`와 run 페이지에 남습니다.
- **재실행은 같은 환경 가정.** 환경 지문 비교가 없고, 로컬 helper 모듈은 스냅샷이 아니라 현재 `scripts/`에서 import됩니다.
- **규모.** 리포트는 매번 전체를 다시 만들고, 데이터셋 프로파일은 전체를 스캔합니다(sha 기준 캐시). 수백 run·수 GB에서는 느려집니다.
  plotly 그림은 JSON째 페이지에 들어갑니다(3 MB 초과는 링크).
- **개인정보.** 텔레메트리에 bash 명령 앞 300자가 로컬로 남습니다. 리포트는 실데이터 미리보기를 담습니다 — 공유 전에 확인하세요.
  프로젝트 `.gitignore`는 `data/`·`report/`를 기본 제외합니다.

## 개발

```sh
cd $PACK
uv sync
uv run pytest -q                                  # kit: 프로젝트·run·재실행·stale·쿼리·리포트 링크
/Users/handochan/dev/aelix-ai/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_extension.py -o addopts=""
uv run ruff check kit tests extension
```

```text
extension/aelix_lab.py    aelix 확장 (stdlib + aelix API, kit/labkit/core.py를 경로로 로드)
kit/labkit/core.py        프로젝트 모델 — stdlib만 (확장과 kit이 공유)
kit/labkit/runner.py      run 실행과 기록        capture.py  입력·차트 캡처 훅
kit/labkit/data.py        import·프로파일·쿼리    report.py   HTML 리포트 (+ assets/)
kit/labkit/__init__.py    스크립트 API           __main__.py kit CLI (JSON 한 줄)
kit/labkit/demo.py        데모 데이터·run        kit/template/  새 프로젝트 파일
bin/lab                   사람용 CLI
```

2026-09-16 확인: kit 35 passed, extension 10 passed(실제 aelix 로더 등록, uv 환경의 import·query·run·재실행·취소 포함).
독립 리뷰에서 나온 16건(차트 정체성, 재실행 판정, stale 계산, 기록 실패 내성, 읽기 전용 쿼리 봉쇄, 계보 오탐,
프로젝트 신뢰 등)을 고치고 항목마다 회귀 시험을 추가했습니다. 설치된 aelix 0.1.0b2에서 RPC 로드와
로컬 모델(ollama) `--print` 세션(lab_status → lab_query → write → lab_run → lab_note)을 실행해 확인했고,
`bin/lab demo` 종단 실행과 리포트 브라우저 확인을 마쳤습니다.

실사용 세션(aelix 세션 텔레메트리 분석, 26 저널 이벤트·6 run) 리뷰에서 나온 7건을 이어서 고쳤습니다:
입력 503개짜리 run이 결과 텍스트·저널 이벤트·리포트를 덮어쓰던 문제(목록·refs·경고에 상한),
`lab_note(refs=["차트-id"])`가 링크되지 않던 문제(짧은 차트 id → `script.py::id` 해석),
차트 이미지 3장 상한 무고지, `lab_import`의 UUID→Binary·중첩 JSON(경고와 텍스트 변환),
이미 import한 외부 입력을 계속 읽을 때의 안내, 생성 run이 없는 데이터셋 표시, CONTEXT.md 주입 한도 경고.
