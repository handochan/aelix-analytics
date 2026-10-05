# Aelix Analytics — 에이전트 시대의 분석

**사람과 에이전트가 검증 가능한 분석과 프로젝트를 함께 이어 가는 새로운 분석 도구·플랫폼을 만듭니다.**
Jupyter Notebook과 BI가 만들어 온 분석 작업방식을 바탕으로, 목적·질문·지표·데이터·처리·실행·시각화·발견·판단·후속 분석을
연결합니다. 대화나 모델이 바뀌어도 근거를 확인하고 분석을 계속할 수 있는 새로운 분석 패러다임이 목표입니다.

분석 방향은 목적과 지표를 중심으로 유지합니다. 모호한 지표의 구체화, 여러 지표의 병행과 파생을 기록하고,
각 탐색·계산·발견을 현재 질문과 다음 판단에 연결하는 것이 설계의 중심입니다.

이 Aelix 확장은 그 비전의 첫 제품 형태이며 **공식 베타 출시를 준비**합니다.
베타 이후의 지속적인 발전을 위해 지표·근거·버전 계약을 보존하고, 정책·저장소·실행·게시 구현을 교체할 수 있는 구조를 설계합니다.
이 저장소는 비교 실험용 `aelix-analytics-baseline`에서 출발해 2026-09-18에 정식 제품 이름인
`aelix-analytics`로 변경했습니다. 과거 평가 문서에는 당시의 baseline 명칭이 이력으로 남아 있습니다.
아래 사용법은 현재 구현 기준입니다. 새 인사이트 화면·재사용 제안·메모리 조회·Reports 탭은 아직 설계 단계입니다.
분석 도중에도 자유 형식 보고서를 만들고, 작성 시점·근거·버전과 컬럼별 반복 보고서 묶음을 관리하는 흐름을 설계합니다.
진행 중인 분석을 팀원에게 전달하는 독립 HTML 빌드와 `publish` 흐름도 설계에 포함합니다.
공식 명칭은 `analytics` 명령·SDK, 분석 인터페이스 `Workspace`, 시각화 객체 `Visualization`, 문서 산출물 `Report`로 정리합니다.
기본 UI·템플릿·속성은 영어, 분석 내용은 사용자 언어를 보존하는 방향입니다. 아직 전환 전이므로 아래 현재 사용법에는 `lab_*`가 남아 있습니다.

사람이 Python·SQL로 직접 분석하는 경로도 정식 사용 시나리오로 설계합니다. 현재도 사람용 `bin/lab` CLI로 Aelix 없이 분석을 실행·기록할 수 있습니다.
베타에서는 모델 없이 프로젝트 생성부터 발견·보고서 기록과 결과 확인까지 완료하고, 사람 → 에이전트 → 사람이 같은 근거를 이어받는지 검증합니다.
[직접 분석 설계](docs/DESIGN-CONTINUITY-AND-REUSE.md#사람이-직접-분석하는-경로)와 [Q17 검증 기준](docs/BETA-QUALITY-GATES.md#사람의-직접-분석과-작성-주체-전환-검증)에 범위를 명시합니다. 노트북 실행 기록 연동과 브라우저 분석 편집기는 후속 범위입니다.

그래프 표현의 도입 방향은 [분석 근거 그래프 제안 0.2](docs/PROPOSAL-EVIDENCE-GRAPH-2026-09-17.md)에 기록되어 있습니다.
각 요소에서 관계를 확인하고 정확한 버전으로 이동하는 흐름, 현재 기록의 한계와 단계별 도입 범위를 담은 후속 제안입니다.

자유로운 Python 분석(DuckDB·Polars·matplotlib·plotly), 프로젝트별 uv 환경, 파일로 읽을 수 있는 기록과 정적 HTML을
유지합니다. 개선의 기준은 분석의 연속성, 근거의 정확성, 사람의 탐색 편의, 에이전트의 문맥·토큰 비용입니다.
모델이 새로운 방법·코드·라이브러리·그림을 선택할 자유를 보존합니다. 지원 설계의 하단 기준은 사용자 지정 **Qwen3.8 27B**이며,
실제 모델·설정별 검증은 아직 필요합니다. 작은 문맥 수치로 모델의 능력이나 분석 범위를 강제 제한하지 않습니다.

인접한 [Aelix Analytics Engine](https://github.com/handochan/aelix-analytics-engine)은 원장·불변 번들·선택·검증·복구의 엄격한 계약을
검증하는 기반 프로젝트입니다. 이 저장소가 사용자 제품과 공식 확장의 대표 이름을 사용합니다.

- 분석은 HTML 리포트로 봅니다. 모든 차트에서 **그 차트를 만든 코드(실행된 바이트 그대로, 줄 강조)**,
  **입력 데이터(sha256, 지금 파일과 비교)**, **차트에 첨부한 데이터(parquet + 미리보기)** 로 바로 이동합니다.
- 데이터는 Parquet, 처리는 DuckDB·Polars입니다.
- 프로젝트 맥락(CONTEXT.md, 버전 기록), 중요 정보(발견·결정·가정·열린 질문), 시간순 이력(journal)을
  파일로 남기고, 리포트와 에이전트 프롬프트에 함께 보여 줍니다.

> 샌드박스가 아닙니다. aelix는 확장 도구를 실행하기 전에 승인을 묻지 않으므로 `lab_run`은 확인 없이
> 코드를 실행합니다. 실데이터에는 이 점을 알고 쓰세요. [한계](#한계)를 먼저 읽으세요.

## 공식 베타 준비 문서

- [제품 방향](docs/PRODUCT.md): 정체성, 분석 발견·재사용 제안·사용 중 문제의 구별, 개선 기준.
- [플랫폼 아키텍처](docs/ARCHITECTURE.md): Strategy 적용 영역, 포트·어댑터·의존 방향, 장기 호환성과 공개 계약, 첫 구현의 검증 기준.
- [용어·기본 언어](docs/TERMINOLOGY-AND-LANGUAGE.md): 제품·도메인·상태·코드·UI의 공통 어휘, 영어 기본값과 다국어 내용 보존.
- [저장소 전체 용어 점검](docs/REVIEW-TERMINOLOGY-2026-09-18.md): CLI·SDK·저장 형식·시각화·템플릿·데모·시험의 변경 목록과 legacy 전환 기준.
- [메모리·skill 벤치마킹](docs/RESEARCH-MEMORY-AND-SKILLS-2026-09-17.md): Hermes 구현, Letta·Graphiti·Mem0와 관련 논문, 현재 코드에서 확인한 한계.
- [제품화 참고 세션 검토](docs/REVIEW-REFERENCE-SESSION-2026-09-18.md): 실제 분석의 전개·오류 복구·지표 형성, 보존할 자유도와 기록 개선점.
- [보고서 산출물 설계](docs/REPORT-ARTIFACTS.md): Reports 탭, 중간·자유 형식 보고서, 시점·버전·근거 보존, 컬럼별 반복 생성과 공유.
- [빌드·공유·게시 설계](docs/PUBLISH-AND-SHARING.md): 독립 HTML/ZIP, 공유 범위, 진행 중 상태, 버전별·최신 게시 주소, 팀 접근과 갱신.
- [맥락·재사용·탐색 설계](docs/DESIGN-CONTINUITY-AND-REUSE.md): 기록·검색·관계 그래프·HTML 동선, 토큰 사용 성능 측정, 배포 구조와 호환성.
- [지표 중심 분석 설계](docs/METRIC-DRIVEN-ANALYSIS.md): 분석 목적과 현재 지표·질문의 연결, 모호한 정의·여러 지표·수정·파생·관측의 구별.
- [Skill 제안 정책](docs/SKILL-PROPOSAL-POLICY.md): 요청 시/상시 모드, 제안 임계값·빈도·별도 비용 측정. 기본 제안은 사용자 요청 시에만 수행.
- [베타 품질 기준과 순서](docs/BETA-QUALITY-GATES.md): 출시 전 검증, 작은 문맥·규모 시험, 재실행·복구·멱등성의 후속 우선순위.
- [기여 가이드](CONTRIBUTING.md): 변경의 개선 효과와 검증 근거를 남기는 방법.
- [프로젝트 운영 정책](docs/PROJECT-OPERATIONS.md): Codex·Claude Code 공통 지침, GitHub Issue/Project 관리, 로컬 재개 상태와 인수인계.

제안된 필드·명령·구조는 구현 완료를 뜻하지 않습니다. 현재 기능과 보장 범위는 [현재 계약](docs/CONTRACTS.md)을 따릅니다.

## 5분 시작

```sh
PACK=/path/to/aelix-analytics                   # 이 저장소를 받은 위치

# 1) 모델 없이 결과물 모양 보기: 합성 데이터 + 예제 run 3개 + 메모 + 재실행 검증
$PACK/bin/lab demo ~/analyses/lab-demo          # 처음엔 uv 환경 준비로 1분 안팎
open ~/analyses/lab-demo/report/index.html

# 2) 그 프로젝트에서 aelix로 이어서 분석
cd ~/analyses/lab-demo
aelix -e $PACK/extension/aelix_lab.py
```

aelix 안에서: `/lab`(현황) · `/lab open`(리포트) · "지역별 성장률 차트에 플롯 데이터도 기록되게 고쳐줘" 같은 요청.

직접 분석에 필요한 것: [uv](https://docs.astral.sh/uv/)(PATH 또는 `AELIX_LAB_UV`). Aelix 연동에는 추가로 aelix 0.1.0b2 이상이 필요합니다.
지원 플랫폼은 Linux·macOS·Windows를 목표로 합니다. 측정한 조합(Windows는 Windows Server 2025의 한 구성)과 Windows의 한계, 아직 실행하지 않은 것(사람이 누른 콘솔 키, Windows의 실제 aelix 세션, 문서를 실제로 여는 프로그램 등)은 [현재 계약](docs/CONTRACTS.md)의 F·G절에 있습니다. Windows 콘솔의 Ctrl+C·Ctrl+Break는 의사 콘솔과 고전 콘솔에서 측정했습니다.
분석 라이브러리는 **프로젝트마다 자기 uv 환경**에 설치되며 aelix 환경은 건드리지 않습니다.

## 설치본(wheel)으로 쓰기

체크아웃 없이 쓰는 방법입니다. Windows에서는 이것이 사람용 명령을 얻는 길입니다(`bin/lab`은 POSIX 셸 스크립트).
배포본은 아직 어디에도 게시하지 않았으므로 체크아웃에서 직접 만듭니다.

```sh
cd $PACK && uv build                                   # dist/aelix_analytics-0.2.0-py3-none-any.whl
uv tool install --python 3.12 dist/aelix_analytics-0.2.0-py3-none-any.whl        # 사람용 `analytics` (Windows: analytics.exe)
uv tool install --python 3.12 aelix==0.1.0b2 --with dist/aelix_analytics-0.2.0-py3-none-any.whl   # aelix와 함께
```

- `analytics`는 명령 이름 말고는 `bin/lab`과 같은 명령·인자·usage를 쓰고 kit의 출력을 바이트 그대로 넘깁니다
  (`analytics --help`). `bin/lab`이 셸의 오류에 맡기는 경우(uv가 없거나 시작할 수 없을 때, 문서를 여는 프로그램이 없을 때,
  작업 디렉터리를 읽을 수 없을 때)에는 자기 문구를 냅니다. 종료 코드가 다른 경우를 포함해 측정한 차이는
  [현재 계약](docs/CONTRACTS.md)의 F절에 있습니다. 이름은 제품 명칭을 따르며, `bin/lab`과 하위 명령의 이름은 명칭
  전환(#9)에서 바뀝니다. Python 3.11 이상이면 됩니다. 분석은 프로젝트의 uv 환경(Python 3.12 이상)에서 돕니다.
- aelix 환경에 설치하면 aelix가 entry point로 확장을 찾습니다. `aelix extension verify aelix-analytics`가 `BOUND`를
  보여 줍니다. `aelix extension install <wheel>`로도 설치할 수 있습니다.
- aelix와 같은 환경에서 `analytics`도 쓰려면 `uv tool install aelix==0.1.0b2 --with <wheel> --with-executables-from
  aelix-analytics`처럼 설치합니다. `analytics`를 따로 설치하면 kit이 두 벌이 되므로 둘을 함께 업그레이드하세요.
- 설치본을 업그레이드한 뒤에는 aelix를 다시 시작하세요(`/reload`로는 새 코드가 로드되지 않습니다).
- aelix를 다시 설치하면(`install.sh`, `uv tool install --force aelix`) 함께 넣은 이 wheel이 빠집니다. 다시 설치하세요.
- **체크아웃의 symlink와 함께 두지 마세요.** 아래 `~/.aelix/agent/extensions/aelix_lab.py` symlink가 있으면 aelix는
  두 사본을 모두 로드합니다. 이 버전 이상의 두 사본은 먼저 로드된 하나만 설정되지만, 이전 버전의 체크아웃은 이를
  알지 못해 hook이 두 번 돕니다. 설치본으로 옮길 때 symlink를 지우세요.

## 확장 등록

| 방법 | 명령 | 언제 |
|---|---|---|
| 설치본 | 위 "설치본으로 쓰기" | 체크아웃 없이, Windows |
| 세션 단위 | `aelix -e $PACK/extension/aelix_lab.py [--lab-project DIR]` | 시험·비교할 때 |
| 전역 등록 | `mkdir -p ~/.aelix/agent/extensions && ln -s $PACK/extension/aelix_lab.py ~/.aelix/agent/extensions/aelix_lab.py` | 계속 쓸 때 (모든 aelix 세션에 도구 6개가 붙음) |
| 해제 | `rm ~/.aelix/agent/extensions/aelix_lab.py` | |

- **복사하지 말고 symlink** 하세요. 확장 파일이 체크아웃의 `src/`(kit, package `aelix_analytics`가 있는 곳)를 찾습니다. 복사했다면
  `AELIX_LAB_KIT=$PACK/src`이고, 체크아웃을 업데이트할 때마다 다시 복사해야 합니다(kit 0.2.0 전의 `AELIX_LAB_KIT=$PACK/kit`은 이제
  "kit not found"로 실패합니다).
- `aelix status`로 로드 여부를 확인할 수 있습니다.
- **디렉터리로 자동 활성화되는 프로젝트는 "내가 만들었거나 고른 것"뿐입니다.** `lab init`·`lab demo`(설치본은 `analytics init`·`analytics demo`)·`/lab use`가
  프로젝트 경로를 `~/.aelix/agent/lab-projects.json`에 적고, 그 목록에 있는 프로젝트 안에서 aelix를 실행할 때만
  활성화됩니다. 남에게 받은 사본은 `/lab use DIR` 한 번이 필요합니다(그 CONTEXT.md가 프롬프트에 들어가고
  `lab_run`이 그 코드를 실행하기 때문입니다).
- Aelix Analytics Engine 어댑터와 이름이 겹치지 않습니다
  (`lab_*`·`/lab` vs `analytics_engine_*`·`/analytics-engine`).
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
/lab init DIR [제목]      새 프로젝트     /lab gap TEXT             사용 중 부족하거나 어색했던 점 기록
/lab demo DIR             데모 프로젝트   /lab context              프롬프트에 주입되는 맥락 블록 보기
/lab use DIR              활성 프로젝트 지정
```

셸에서는 같은 기능을 체크아웃의 `bin/lab`이나 설치본의 `analytics`로 씁니다(`lab status`, `lab run scripts/x.py --title …`, `lab query "SQL"` 등. 설치본에서는 `lab` 자리에 `analytics`).

### 스크립트 API

```python
import aelix_analytics as analytics

sales = analytics.read("sales")                                    # Polars DataFrame (data/raw/sales.parquet)
monthly = analytics.sql("SELECT ... FROM sales GROUP BY ALL").pl() # 모든 데이터셋이 view인 DuckDB
analytics.save(monthly, "monthly_revenue")                         # data/derived/monthly_revenue.parquet
analytics.chart(fig, "monthly-revenue", title="…", caption="…", data=monthly)   # matplotlib/plotly
analytics.note("finding", "…")                                     # 이 run에 연결된 journal 기록
```

run을 시작한 명령(`analytics`, `bin/lab`, aelix 도구)이 이 package를 run에 줍니다. kit 0.2.0 전의 이름 `import labkit as lab`은
더는 import되지 않습니다: 그런 스크립트는 새 이름을 알려 주는 오류로 실패하니 import 줄을 고치세요. 옛 kit으로 기록한 run을
그대로 다시 실행하려면 그 kit이 필요합니다. 설치본의 `python -m aelix_analytics`는 거절되므로 셸에서는 `analytics`를 씁니다.

SDK를 쓰지 않은 **관용 코드도 기록됩니다**: `pl.read_parquet("data/…")`, `duckdb.sql("… FROM 'data/….parquet'")`,
`plt.show()`, plotly `fig.show()`. 차트의 **명시적인 첨부 표**는 `analytics.chart(..., data=df)`로 남깁니다.
첨부 표가 요약 통계나 일부 계열이면 그림 전체를 재구성하는 데 입력·코드도 필요하며, 첨부 자체가 완전한 플롯 대응을 보장하지 않습니다.
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
- **입력 감지(최선 노력)**: 런타임 훅(Polars 읽기, DuckDB 읽기·SQL 파일 리터럴, `analytics.read/scan/sql`),
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
- **프롬프트 주입 — 섹션 단위 점진적 공개**: 매 요청 전에 `CONTEXT.md` + 해결되지 않은 핵심 정보 + 열린 항목을
  시스템 프롬프트 뒤에 붙입니다. CONTEXT.md는 통째로 넣지 않고 **`##` 섹션 단위**로 넣습니다 —
  `<!-- 항상 -->`을 붙인 섹션과, 앞에서부터 예산(4,000자)이 허락하는 섹션이 본문째 들어가고,
  **나머지는 제목·줄 범위만** 들어갑니다(`read CONTEXT.md offset N limit M`). 줄 번호는 aelix의 read 도구가
  세는 그대로입니다(`\n`으로만 나누고, 앞의 빈 줄과 CRLF 파일도 같게). 문장 중간에서 잘리는 일이 없고,
  무엇이 빠졌는지는 `lab_status`와 리포트 맥락 탭이 이름으로 알려 줍니다. 순서를 바꾸거나 `<!-- 항상 -->`을
  붙이는 것이 통제 수단입니다. 자주 바뀌는 현황은 `lab_status`로 봅니다.
- **빠진 것과 다시 읽을 위치**: 첫 `##` 위의 머리말이 4,000자를 넘으면 앞 4,000자만 들어가고, 뺀 글자 수와 머리말 전체의 줄 범위
  (`read CONTEXT.md offset 1 limit N`)가 붙습니다. 핵심 정보와 열린 항목은 각각 최근 12개가 들어가고(글은 300자까지),
  더 있거나 잘린 글이 있으면 몇 개를 뺐는지와 `journal.jsonl` 경로를 적습니다. `lab_status`·`lab_run`·`lab_query` 등의
  응답도 줄이거나 자른 자리에 개수와 위치(journal, run의 `run.json`과 로그 파일, 데이터셋의 `meta.json`)를 적습니다.
  위치는 텍스트를 만든 때의 파일을 가리키므로, 그 뒤에 CONTEXT.md를 고치면 줄 범위가 어긋날 수 있습니다.
  자세한 것은 [계약 A1](docs/CONTRACTS.md#a1-모델에-가는-텍스트의-생략과-다시-읽을-위치)입니다.

현재 4,000자는 일반 섹션 선택의 기준이며 **전체 프롬프트 주입량의 상한은 아닙니다**. 고정 섹션은 이를 넘길 수 있고,
생략 섹션 목록과 journal 요약도 추가됩니다. 공식 베타 설계는 전체 사용량과 분석 품질을 함께 측정하며,
토큰 목표를 강제 제한으로 사용하지 않습니다.

## 기록 상태와 사용 중 문제 — 현재 인터페이스

- 쓰다가 부족한 점은 바로 `/lab gap <내용>`. 모델도 막히면 `lab_note(kind="gap")`을 남기도록 지침이 들어 있습니다.
- 확장이 **모든 도구 호출**을 `.lab/telemetry.jsonl`에 남기고, 규약 우회 의심(`python_via_bash`, `wrote_managed_path`)을 표시합니다.
- 리포트 **평가** 탭: gap 목록, 기록 품질 신호(입력 미감지 run, 플롯 데이터 없는 차트, 프로젝트 밖 입력, stale 차트, 재실행 결과),
  도구별 호출·오류·시간, 우회 의심 호출.
- 기존 비교 실험의 축과 시나리오: [과거 평가 가이드](docs/EVALUATION.md).

공식 베타에서는 이 탭을 **인사이트**로 전환해 분석 발견과 재사용 제안을 연결하고, 기록 품질·도구 사용은
진단 정보로 유지할 계획입니다. 기존 gap은 사용 중 문제로 보존합니다. [설계](docs/DESIGN-CONTINUITY-AND-REUSE.md)를 참고하세요.

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
- **현재 공유 범위.** `report/`는 프로젝트의 차트·데이터 파일을 상대 경로로 참조하므로 그 폴더만으로 독립 공유를 보장하지 않습니다.
  독립 `build`·`publish`는 위 공유 설계의 후속 구현이며 현재 명령이 아닙니다.

## 개발

```sh
cd $PACK
uv sync
uv run pytest -q                                  # kit, 런처, 배포본(wheel을 빌드해 새 환경에 설치)
uv run --isolated --no-project --python 3.12 --with aelix==0.1.0b2 --with pytest==9.1.1 --with pytest-timeout==2.4.0 \
  python -m pytest -q -p no:cacheprovider --timeout 300 --timeout-method=thread tests/test_extension.py tests/test_usage_report.py   # aelix 통합 (CI와 같은 환경)
AELIX_ANALYTICS_TEST_INSTALL=1 uv run pytest -q tests/test_packaging.py   # aelix 0.1.0b2와 실제 uv로 끝까지 (네트워크)
uv run ruff check tests extension src tools
```

```text
src/aelix_analytics/          kit이자 배포본의 package. src/가 자식에게 주는 실행 루트(설치본은 aelix_analytics/kit/의 사본)
  __init__.py                 스크립트 API (모듈 수준에서는 stdlib만; kit 모듈은 함수를 부를 때 import)
  __main__.py                 kit CLI (끝 줄이 JSON; 설치된 package 자신으로는 실행 거절)
  core.py                     프로젝트 모델 — stdlib만 (확장과 kit이 공유)
  _platform.py                플랫폼별 잠금·교체·프로세스·run 중단 — stdlib만 (core와 함께 로드)
  relations.py                기록의 관계·해석 상태·상태 재생 읽기 모델(ADR-0006) — stdlib만, 아직 어느 명령·화면도 쓰지 않음
  runner.py  capture.py       run 실행과 기록, 입력·차트 캡처 훅
  data.py    report.py        import·프로파일·쿼리, HTML 리포트 (+ assets/)
  demo.py                     데모 데이터·run (+ demo_scripts/)    template/  새 프로젝트 파일
  cli.py  _install.py         `analytics` 런처, 설치된 실행 루트 찾기    aelix-plugin.toml  manifest
extension/aelix_lab.py        aelix 확장 (stdlib + aelix API, 실행 루트의 core·_platform을 경로로 로드; 설치본의 aelix_analytics/extension.py)
bin/lab                       사람용 CLI (체크아웃, POSIX)
```

2026-09-16 확인: kit 39 passed, extension 10 passed(실제 aelix 로더 등록, uv 환경의 import·query·run·재실행·취소 포함).
독립 리뷰에서 나온 16건(차트 정체성, 재실행 판정, stale 계산, 기록 실패 내성, 읽기 전용 쿼리 봉쇄, 계보 오탐,
프로젝트 신뢰 등)을 고치고 항목마다 회귀 시험을 추가했습니다. 설치된 aelix 0.1.0b2에서 RPC 로드와
로컬 모델(ollama) `--print` 세션(lab_status → lab_query → write → lab_run → lab_note)을 실행해 확인했고,
`bin/lab demo` 종단 실행과 리포트 브라우저 확인을 마쳤습니다.

실사용 세션(aelix 세션 텔레메트리 분석, 26 저널 이벤트·6 run) 리뷰에서 나온 7건을 이어서 고쳤습니다:
입력 503개짜리 run이 결과 텍스트·저널 이벤트·리포트를 덮어쓰던 문제(목록·refs·경고에 상한),
`lab_note(refs=["차트-id"])`가 링크되지 않던 문제(짧은 차트 id → `script.py::id` 해석),
차트 이미지 3장 상한 무고지, `lab_import`의 UUID→Binary·중첩 JSON(경고와 텍스트 변환),
이미 import한 외부 입력을 계속 읽을 때의 안내, 생성 run이 없는 데이터셋 표시, CONTEXT.md 주입 한도 경고.

## 라이선스

[Apache License 2.0](LICENSE).
