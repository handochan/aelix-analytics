# Q17 안내서 — 사람만의 분석과 실제 에이전트 인계 (#23)

[베타 품질 기준 Q17](../../docs/BETA-QUALITY-GATES.md#사람의-직접-분석과-작성-주체-전환-검증)을 소유자가 직접 수행하는 순서입니다.
명령은 kit 0.25.0에 있는 것만 씁니다. 준비·측정·대조는 [`tools/human_e2e.py`](../human_e2e.py)가 하고, 결과는
에이전트가 `docs/verification/`에 정리합니다. 안내대로 되지 않거나 막히면 그 자체가 Q17의 결과이므로, 우회하기 전에 메모해 주세요.

아래에서 `PACK`은 이 저장소의 체크아웃, `Q17`은 준비된 환경의 폴더입니다(예: `PACK=~/dev/aelix-extensions/aelix-analytics`,
`Q17=~/aelix-q17`).

## 0. 준비 (에이전트가 해 둠)

```sh
cd $PACK && uv run --locked python tools/human_e2e.py prepare $Q17
```

- 체크아웃의 clean HEAD로 출시할 wheel을 만들어 새 HOME에 설치합니다(`uv tool install`). 분석 라이브러리는 미리 받아 둡니다.
  여기까지만 네트워크를 씁니다.
- 합성 데이터 `$Q17/data/orders.csv`: 한 쇼핑몰의 일별 주문입니다(2026-06-01–09-15, 지역 4개 × 채널 2개, 주문·매출·환불).
- `$Q17/prepare.json`에 설치한 것과 막을 것을 적습니다. 막는 것은 모든 네트워크 연결(로컬 모델 서버 포함), 이름 조회,
  모델 프로그램(aelix·codex·claude·ollama 등)의 실행, 그 설정·로그인 폴더(`~/.aelix`·`~/.codex` 등) 읽기입니다.

## Q17-a — 모델 없이 사람만으로

### 셸 열기

```sh
cd $PACK && uv run --locked python tools/human_e2e.py shell $Q17
```

프롬프트가 `[q17a]`로 바뀝니다. 이 셸 안의 모든 명령이 막힌 환경에서 돕니다. `exit`로 끝내면 그 세션에서 막힌 시도가 몇 번
있었는지 출력됩니다(기대값 0). 스크립트·문서는 평소 편집기로 고쳐도 됩니다. 편집기는 이 셸 밖에서 돌아도 됩니다. 명령만 이
셸에서 실행해 주세요. 확인용으로 `which aelix`를 입력하면 `aelix not found`가 나와야 합니다.

### 1. 프로젝트·맥락·데이터·SQL

```sh
analytics init q3-sales --title "3분기 매출 점검" --goal "3분기 매출이 줄었다는 보고를 확인하고 원인 후보를 찾는다"
cd q3-sales
analytics status
```

편집기로 `CONTEXT.md`의 `## Analysis focus` 아래에 질문을 적습니다. 예:

```markdown
- 어느 지역·채널에서 매출이 줄었나?
- 9월 매출 감소는 실제인가?
```

```sh
analytics import ../../data/orders.csv --name orders --description "일별 주문(합성, 2026-06-01~2026-09-15)"
analytics query "SELECT substr(CAST(date AS VARCHAR), 1, 7) AS month, sum(revenue) AS revenue, count(DISTINCT date) AS days FROM orders GROUP BY ALL ORDER BY 1"
analytics query "SELECT region, channel, substr(CAST(date AS VARCHAR), 1, 7) AS month, sum(orders) AS orders FROM orders WHERE region = '부산' GROUP BY ALL ORDER BY ALL"
```

SQL은 자유롭게 바꿔 보세요. 출력 마지막 줄이 JSON입니다.

### 2. Python 스크립트

**SDK 경로, 일부러 틀린 첫 시도.** `scripts/monthly.py`를 만듭니다. 마지막 `ax.plot`의 `"revenu"`가 틀린 열 이름입니다.

```python
import aelix_analytics as analytics
import matplotlib.pyplot as plt

monthly = analytics.sql("""
    SELECT substr(CAST(date AS VARCHAR), 1, 7) AS month, region, channel,
           sum(orders) AS orders, sum(revenue) AS revenue
    FROM orders GROUP BY ALL ORDER BY ALL
""").pl()
analytics.save(monthly, "monthly_by_segment")

fig, ax = plt.subplots(figsize=(8, 4))
for (region, channel), part in monthly.group_by(["region", "channel"], maintain_order=True):
    ax.plot(part["month"], part["revenu"], marker="o", label=f"{region}·{channel}")
ax.set_title("지역·채널별 월 매출")
ax.legend(ncol=4, fontsize=8)
analytics.visualize(fig, "monthly-revenue", title="지역·채널별 월 매출", caption="9월은 15일까지", data=monthly)
```

```sh
analytics run scripts/monthly.py --title "월별 매출(첫 시도)"     # 실패해야 합니다(ColumnNotFoundError)
```

`"revenu"`를 `"revenue"`로 고치고 다시 실행합니다.

```sh
analytics run scripts/monthly.py --title "월별 매출"
analytics status          # runs: 실패한 첫 run이 error로 남고 새 run이 ok인지
```

**일반 라이브러리 경로(SDK 없이).** `scripts/busan_daily.py`:

```python
import matplotlib.pyplot as plt
import polars as pl

orders = pl.read_parquet("data/imported/orders.parquet")
busan = (orders.filter(pl.col("region") == "부산")
         .group_by("date", "channel").agg(pl.col("orders").sum()).sort("date"))
fig, ax = plt.subplots(figsize=(8, 4))
for channel in ("앱", "웹"):
    part = busan.filter(pl.col("channel") == channel)
    ax.plot(part["date"], part["orders"], label=channel)
ax.set_title("부산 일별 주문")
ax.legend()
plt.show()
```

```sh
analytics run scripts/busan_daily.py --title "부산 일별 주문"
```

`analytics status`의 `runs`·`charts`에서 run ID(`20261021-…-busan-daily` 모양)와 Visualization 이름
(`scripts/busan_daily.py::chart-1`, `scripts/monthly.py::monthly-revenue`)을 확인합니다. 아래에서 `BUSAN`·`MONTHLY`는
그 run ID입니다.

### 3. 기록

```sh
analytics record finding "부산 웹 주문이 7월 20일 무렵부터 약 35% 줄었다. 앱은 그대로다" --ref BUSAN --ref scripts/busan_daily.py::chart-1
analytics record question "9월 매출 감소는 9월 데이터가 15일까지라서 생긴 것인가?" --ref MONTHLY
analytics record question "환불이 늘어난 지역·채널이 있나?"
analytics record todo "부산 웹 결제 단계의 오류 기록을 확인한다"
```

응답 JSON의 `event.id`(`e8` 모양)와 `refs`의 `version_binding`(`author`는 그 run, `inferred`는 그 이름의 최신 성공 버전)을
봐 둡니다. 숫자·문장은 직접 본 결과에 맞게 바꿔 써도 됩니다. 마지막 질문 하나는 열어 둡니다(Q17-b에서 에이전트가 이어받을 거리).

**SDK로 기록.** `scripts/same_days.py`:

```python
import aelix_analytics as analytics

same = analytics.sql("""
    SELECT substr(CAST(date AS VARCHAR), 1, 7) AS month, sum(revenue) AS revenue
    FROM orders WHERE day(date) <= 15 GROUP BY ALL ORDER BY 1
""").pl()
analytics.save(same, "first_half_month_revenue")
print(same)
change = same["revenue"][-1] / same["revenue"][-2] - 1
analytics.record("finding", f"1~15일끼리 비교하면 9월 매출은 8월보다 {change:.1%} 다르다")
analytics.record("assumption", "9월 16일 이후 데이터는 아직 들어오지 않았다고 본다")
```

```sh
analytics run scripts/same_days.py --title "같은 일수 비교"
analytics record resolve "같은 일수로 보면 9월 감소는 기간 차이였다" --resolves 9월질문의ID --ref SAME
analytics record decision "월 비교는 같은 일수(1~15일)끼리 한다" --ref SAME
```

`SAME`은 방금 run의 ID, `9월질문의ID`는 위 9월 질문의 `e…`입니다.

### 4. 중간 Report, 분석 계속, 새 버전

```sh
mkdir -p report-drafts/q3
cp runs/MONTHLY/visualizations/monthly-revenue.png report-drafts/q3/
```

편집기로 `report-drafts/q3/요약.md`를 씁니다. 예:

```markdown
# 3분기 매출 중간 보고

- 부산 웹 주문이 7월 20일 무렵부터 약 35% 줄었다.
- 9월 전체 매출 감소는 9월 데이터가 15일까지라서 생긴 것으로 보인다.

![월 매출](monthly-revenue.png)
```

근거 파일 `../based-on.json`(지금 CLI는 근거를 정해진 JSON 모양으로만 받습니다. `BUSAN`·`MONTHLY`를 실제 ID로):

```json
{"as_of_seq": null, "context_version": null,
 "refs": [{"type": "run", "run_id": "BUSAN", "relation": "cites", "basis": "declared", "version_binding": "author"},
          {"type": "run", "run_id": "MONTHLY", "relation": "cites", "basis": "declared", "version_binding": "author"}]}
```

```sh
analytics reports save q3-review --files report-drafts/q3 --entry 요약.md --title "3분기 매출 중간 보고" --previous none --based-on ../based-on.json
```

응답의 `report.revision_id`가 첫 버전 `REV1`입니다. 이제 앞의 발견을 정정합니다(직접 본 수치로).

```sh
analytics record finding "부산 웹 주문 감소는 약 32%다(6월 대비 8월). 7월 20일 전후로 나뉜다" --ref BUSAN --refs-json '[{"type": "event", "event_id": "부산발견의ID", "relation": "supersedes"}]'
```

`요약.md`를 고칩니다(감소 폭 정정, 같은 일수 비교 한 줄 추가). 바꾼 이유를 `../meta2.json`에 씁니다.

```json
{"change_reason": "부산 감소 폭 정정, 같은 일수 비교 추가"}
```

```sh
analytics reports save q3-review --files report-drafts/q3 --entry 요약.md --title "3분기 매출 중간 보고" --previous REV1 --metadata ../meta2.json
analytics reports list
analytics reports show q3-review@REV1 --file 요약.md      # 첫 버전의 본문이 그대로인지(file.data는 base64)
```

### 5. Workspace, 그리고 다시 열기

```sh
analytics render
analytics open
```

브라우저에서 확인합니다.

- Overview → run `월별 매출` → 그림 → 그 데이터·코드
- 발견 → 근거 run·그림
- Reports 탭 → `3분기 매출 중간 보고` → 두 버전, 각 버전의 근거와 본문(별도 문서로 열림)
- 실패한 첫 run이 실패로 보이는지

셸을 `exit`로 끝냅니다(그 세션의 결과가 출력됩니다). 잠시 뒤 **새 셸**을 열어 다시 들어갑니다.

```sh
cd $PACK && uv run --locked python tools/human_e2e.py shell $Q17
cd q3-sales
analytics status
analytics context
analytics checkpoint "오늘 확인한 것과 다음 할 일을 한두 문장으로"
exit
```

목적·발견·열린 질문·다음 할 일이 이 출력만으로 다시 잡히는지 봅니다.

### 기록할 것

`$Q17/owner-notes.md`(준비 때 만들어 둠)에 단계마다 적어 주세요.

- 걸린 시간(대략)
- 막힌 곳과 그때 본 도움말·오류 문구
- 안내와 다르게 한 것(수동 우회)
- 헷갈린 점

명령과 시각은 셸 history(`$Q17/env/home/.zsh_history`)에도 남습니다.

## Q17-b — 사람 → 에이전트 → 사람 (모델마다)

모델은 `gpt-6.1-sol`(소유자의 aelix 로그인)과 `qwen3.8-27b-local`(이 Mac의 ollama)입니다. 모델마다 Q17-a 프로젝트의
**사본**에서 따로 합니다. 원본은 그대로 둡니다.

1. **환경 준비(에이전트가 함):**
   ```sh
   uv run --locked python tools/human_e2e.py agent-env $Q17 --model MODEL --project $Q17/work/q3-sales
   ```
   그러면 `$Q17/agents/MODEL/`이 생깁니다. 안에는 aelix 0.1.0b2와 같은 wheel, 새 agent 디렉터리·HOME, 프로젝트 사본, `start.sh`가 있습니다.
   Qwen은 먼저 [로컬 Qwen 서빙](../model_eval_data/README.md#로컬-qwen-서빙)의 `ollama serve`와 `serving-check`를 합니다.
2. **소유자가 세션 시작:** 새 터미널에서 `$Q17/agents/MODEL/start.sh`를 실행합니다. aelix가 프로젝트 사본에서 열립니다.
   - `/analytics use .`를 입력해 이 프로젝트를 고릅니다.
   - [`q17b-request.md`](q17b-request.md)의 요청 글을 그대로 붙여 넣습니다.
   - 이전 대화나 Q17-a의 설명은 붙여 넣지 않습니다.
   - 에이전트가 묻는 것에는 답해도 됩니다(그 내용을 메모).
   - 끝나면 aelix를 닫습니다(`/exit` 또는 Ctrl+D).
3. **대조(에이전트가 함):** 사본의 이전·이후 snapshot을 `compare --expect-actor agent`로 비교하고, `agent-usage`로 토큰·요청 수·도구 호출을 셉니다.
4. **소유자의 검토·수정(모델 없이):**
   ```sh
   cd $PACK && uv run --locked python tools/human_e2e.py shell $Q17 --cwd $Q17/agents/MODEL/project
   analytics status
   analytics open
   ```
   에이전트의 기록(작성 주체 `agent`)과 run을 읽고 판단을 새 기록으로 남깁니다. 예: 받아들이는 `decision`, 정정하는 `finding`
   (`supersedes`), 질문을 닫는 `resolve`, 필요하면 Report 새 버전. 끝나면 `exit`.
5. **대조(에이전트가 함):** `compare --expect-actor human`과 `check`(이 검토 세션도 0회여야 함).

## 끝난 뒤 (에이전트가 함)

Q17-a와 Q17-b의 결과·증거·미실행 범위를 따로 `docs/verification/`에 정리하고 #23의 완료 조건을 판정합니다. 모델 사용량은
Q17-b에만 셉니다.
