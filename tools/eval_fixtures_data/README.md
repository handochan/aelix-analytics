# 평가 fixture (Issue #41)

[`tools/eval_fixtures.py`](../eval_fixtures.py)가 만드는 평가용 합성 프로젝트의 안내다. [베타 품질 기준](../../docs/BETA-QUALITY-GATES.md)의
Q9(S/M 규모), Q10(분석 검사 예제), Q13(참고 세션 재현)이 이 fixture를 쓴다. fixture는 저장소에 커밋하지 않고 필요할 때 만든다. 같은 seed는
같은 환경에서 같은 바이트를 만든다(아래 휘발 필드 제외).

## 만들기

개발 환경에서 실행한다(분석 라이브러리가 필요하다). `--out`은 새 디렉터리나 빈 디렉터리여야 한다.

```sh
uv run --locked python tools/eval_fixtures.py scale --scale S --seed 0 --out DIR
uv run --locked python tools/eval_fixtures.py scale --scale M --seed 0 --out DIR
uv run --locked python tools/eval_fixtures.py q10 --seed 0 --out DIR
uv run --locked python tools/eval_fixtures.py q13 --seed 0 --out DIR
uv run --locked python tools/eval_fixtures.py check DIR
uv run --locked python tools/eval_fixtures.py environment --out FILE --disk DIR --cache-state cold --browser chrome=141.0
```

`--format`은 `v1`(지금 kit이 쓰는 `lab.*/1` 기록)만 된다. `v2`는 #13의 writer가 병합된 뒤 같은 계획으로 쓴다. 종료 코드는 0(성공),
1(kit 오류나 `check`가 찾은 문제), 2(사용법 오류: 비어 있지 않은 `--out`, 아직 없는 형식)다. 생성기는 Polars를 한 스레드로
돌린다(`POLARS_MAX_THREADS=1`): 여러 스레드의 group_by는 부동소수 합의 순서가 실행마다 달라 마지막 비트가 바뀐다. Python
API(`generate_scale`·`generate_q10`·`generate_q13`·`check`·`tree_digest`·`environment_record`)는 Polars를 여러 스레드로 이미 불러온
프로세스에서는 생성을 거절한다. 생성 중에는 신뢰 목록과 uv를 임시 디렉터리로 돌려 사용자의 것을 건드리지 않는다. 생성이 실패하면
(종료 코드 1) 그 생성이 `--out`에 쓴 것을 지우므로 같은 `--out`으로 다시 실행할 수 있다.

`check`는 kit의 읽기로 판정을 다시 계산한 뒤 kit의 파생물(`project/.lab/cache/`, `project/report/`)을 비워 fixture를 생성 직후처럼
cold로 둔다. 그 뒤 warm 측정이 필요하면 측정 쪽에서 한 번 읽어 데운다. `environment`의 디스크 기록은 `--disk`(기본: `--out` 파일의
폴더)의 디스크다. 측정하는 fixture의 디렉터리를 준다.

## 출력

```text
DIR/
  manifest.json      생성기·seed·규모·형식·개수·크기·사례와 판정·휘발 필드·트리 digest (같은 환경이면 바이트까지 같음)
  environment.json   이 생성의 기기·디스크·Python·SQLite·브라우저·라이브러리·캐시 상태·단계별 시간 (휘발)
  project/           lab.json 프로젝트 — kit으로 그대로 연다
  expected.json      q10·q13: 기대 판정 (schemas/expected.schema.json)
  input/             가져온 원본(규모: sales_kr.csv와 센서 Parquet, q10·q13: CSV). 가져오기 기록의 source.path가 이 파일이다
```

schema는 [`schemas/`](schemas/)에 있다: [manifest](schemas/manifest.schema.json), [expected](schemas/expected.schema.json),
[environment](schemas/environment.schema.json).

## 규모

| | runs | events(journal 줄) | datasets(가져온 원본) | 원본 크기(seed 0) | 생성 시간(참고) |
|---|---|---|---|---|---|
| S | 30 (kit이 실행 3) | 300 | 5 | 2.4 MB | 약 1초 |
| M | 300 (kit이 실행 3) | 10,000 | 20 (4,000,000행 4개) | 217 MB(1 GB 이내) | 약 8초 |

생성 시간은 macOS arm64(Apple Silicon)·로컬 SSD에서 잰 값이며 보장이 아니다. 실제 값은 각 fixture의 `environment.json`에 있다.
파생 표(`data/derived/`)는 datasets에 세지 않는다(`core.status`의 `counts.datasets`는 둘을 합친다). 원본 크기는 프로젝트의
`data/raw/`이고, 같은 크기의 원본 파일이 `input/`에 한 번 더 있다(M의 fixture 전체는 약 440 MB).

## 기록을 만드는 방법

기록의 바이트는 kit의 /1 writer 함수가 쓴다. 프로젝트 생성(`core.init_project`)·가져오기(`data.import_file`)·journal 기록(`core.add_note`,
`core.append_event`)은 모두 실제 kit이다. q10·q13의 모든 run과 규모 fixture의 run 3개는 kit이 스크립트를 실행한다(`runner.execute`,
스크립트는 [`scripts/`](scripts/)). 규모 fixture의 나머지 run은 실행하지 않고 run 디렉터리와 기록을 만들어 kit의 `core.write_json`으로
쓴다(실행한 run과 키·순서가 같은지 `tests/test_eval_fixtures.py`가 확인). 그 run의 코드는 기록이 말하는 입력을 읽고(파생 표는 경로
문자열로 읽어 기록의 `via`에 `scan`이 있다), 기록의 출력을 저장하고, 기록의 차트를 그 줄에서 그리며, 실패한 run은 오류가 가리키는 줄에서
데이터셋에 없는 열을 쓴다(시험이 확인). 다만 출력의 바이트는 seed가 만든 것이라 그 코드를 실행한 결과가 아니다. 재실행 비교(`rerun_of`)나
코드가 실제로 낸 값의 평가에는 kit이 실행한 run을 쓴다. 생성하는 동안 kit의 시계는 seed가 정한 계획의 시계다(+09:00).

## 사례 (규모 fixture)

`manifest.json`의 `cases[]`마다 기록 id·경로(`records`)와 실제 reader로 확인할 판정(`checks`)이 있다. `check` 명령과 시험이 그 판정을
`relations.read`·`core.status`·`core.Freshness`로 다시 계산한다. /1에는 정정(`supersedes`)·철회(`record_update`)가 없어, /1로 쓸 수 있는
모습으로 남기고 판정은 [ADR-0006](../../docs/decisions/0006-common-record-contract.md)의 읽기를 따른다.

| 사례 | 필수 | /1에서의 모습 | 판정 |
|---|---|---|---|
| `correction` | 예 | 앞쪽 발견과, 한참 뒤 `refs.resolves`로 그것을 가리킨 "정정: …" 발견 | 참조 `resolved`·확정 연결, 원래 발견의 상태 `current`, kit은 원래 발견을 해결된 것으로 읽음 |
| `withdrawal` | 예 | 결정과, 뒤의 `kind=resolve` "철회: …" | 같은 방식 |
| `unpreserved_data` | 예 | 덮어쓴 파생 표, 지운 파생 표와 그것을 읽은 run, 파일을 지운 차트, 해시 없는 입력, run 뒤 고친 스크립트 | `content_changed`, `missing`(`file_absent`), `unknown`(`no_digest`), 코드 `changed` |
| `same_chart_id` | 예 | 여러 run의 같은 차트 id, 두 스크립트의 `trend`, 버전 없는 key 인용, writer가 최신 key로 바꾼 이름 `region-total` | key별 버전 2개 이상, 참조 `version_unbound` |
| `reference_cycle` | 예 | 같은 시각에 시작해 서로의 출력을 읽은 두 run, 뒤 기록을 먼저 인용한 기록 | 상류 탐색 `cycle`, 앞으로의 참조 `missing`(`record_absent`) |
| `long_korean` | 예 | 6,000자 기록(4,000자로 저장), 섹션이 많은 긴 CONTEXT, 한국어 제목·열 | 저장 4,000자, 맥락에서 빠진 섹션 1개 이상 |
| `display_safety` | 아니오 | HTML·스크립트·경로·템플릿처럼 보이는 글 (Q10 표시) | 글이 쓴 그대로 |
| `old_finding` | 아니오 | journal 앞쪽의 중요한 발견 | 최근 목록 밖, `current` |
| `question_closed`·`failed_run`·`ambiguous_producer` | 아니오 | 닫힌 질문, kit의 오류 run, 같은 바이트의 두 생산자 | `resolved`, `error`·`complete`, `ambiguous` |

## Q10과 Q13의 기대 판정

- **Q10**: 집계 키·단위·분모·기간마다 결함 있는 분석(`fail`)과 바른 분석(`pass`) 한 쌍, 8 run. 예제마다 스크립트·run·주장 기록·이유와
  근거 수치(주장한 값과 바른 값 등)가 있고, 수치는 프로젝트의 Parquet에서 다시 계산된다. 근거의 비율은 6자리로 반올림해 적지만 주장 글은
  스크립트처럼 반올림하지 않은 비율로 만든다. 9월의 하루 세션은 8월의 1.25배이고, 부분 월 비교가 감소·같은 일수 비교가 증가가 아닌
  데이터는 같은 seed의 난수로 다시 뽑는다(seed 0–19를 시험이 확인).
- **Q13**: [참고 세션](../../docs/REVIEW-REFERENCE-SESSION-2026-09-18.md)의 EDA → 스텝 프로파일 → 시점별 점수 → 전체 센서 그림을 run
  6개(성공 4·오류 2)로 재현한다. `facts`(점수 표·초과 샘플·최대 |z|·표시된 트레이스의 밴드 밖 샘플·트레이스 평균 이탈), `claims`(주장과
  판정 `supported`·`false`·`unverified`), `records`(실패한 run의 부분 산출물과 현재 바이트의 생산자, 첨부 표의 범위), `metrics`(지표
  정의의 집계 단위·기준 모집단), `session`(사용자 요청 3개와 단계별 기대 행동). 같은 코드·답을 요구하지 않는다.

## 휘발 필드와 digest

`manifest.json`의 `digest`는 `manifest.json`·`environment.json`·kit의 파생물(`project/.lab/cache/`, `project/report/`)을 뺀 모든 파일의
(경로, 내용 sha256)를 경로순으로 해시한 값이다. 아래 필드는 `"<volatile>"`로 바꿔 해시한다.

- `project/runs/*/run.json`의 `duration_s`(실행한 run의 잰 시간)와 `env`(Python 경로·플랫폼·라이브러리 버전).
- `project/data/raw/*.meta.json`의 `source.path`(`input/`의 원본 절대 경로)와 `source.modified_at`(계획의 시각이 기기의 시간대로 적힘).
- `project/runs/*/stderr.txt`에서 traceback이 스크립트를 가리키는 경로의 프로젝트 디렉터리까지(`File "…/project/`, Windows는
  `File "…\project\`)를 `File "<project>/`로 읽는다. 그래서 옮기거나 복사한 fixture도 `check`를 통과한다.

같은 `--out`에 다시 만들면(앞의 것을 옮긴 뒤) 실행한 run의 `duration_s` 말고는 모든 파일의 바이트가 같다(시험이 확인).

환경(체크아웃·Python·duckdb·polars·plotly 버전)이 다르면 Parquet·차트 JSON의 바이트가 달라질 수 있다. 생성한 fixture는 kit 캐시가 없는
cold 상태다. OS 파일 캐시는 비우지 않는다. 측정할 때는 `environment` 명령으로 그때의 환경과 `--cache-state`를 따로 남긴다.

## 쓰는 곳

- #15(제한 조회·재개): S·M의 조회 지연(warm/cold·재구축)과 사례의 검색·회수 정답.
- #18(관계 UI): S·M의 첫 화면·Workspace 재구축 비용, S의 관계 탐색, `display_safety`.
- #24(출시 리허설): Q9 결과표의 S/M과 환경 기록, Q10 예제와 기대 판정, Q13 과제(#40의 하네스와 함께).
