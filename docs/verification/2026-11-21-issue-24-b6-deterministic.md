# Issue #24 B6 — 결정적 게이트와 Q17-a를 출시 후보 0.32.1로 (2026-11-21)

[Issue #24](https://github.com/handochan/aelix-analytics/issues/24)에서 모델이 필요 없는 게이트를 0.32.1 후보로 다시 잰 기록이다([릴리스 절차](../../CONTRIBUTING.md#릴리스-절차) 3단계).
방법은 0.32.0의 [B6 기록](2026-10-31-issue-24-b6-deterministic.md)·[Q17-a 기록](2026-10-31-issue-24-q17a.md)과 같다. 이번 판에서 새로 생긴 비용
(#169의 하위 폴더 찾기, #171의 bash 관찰)도 쟀다. 수치·명령·해시의 정본은 [JSON](2026-11-21-issue-24-b6-deterministic.json)이다.
fixture와 그 사본, Workspace, Q17-a의 환경과 원시 측정 디렉터리는 이 기기에만 두고 커밋하지 않았다.

## 후보와 측정의 연결

후보는 main `494d1ce`(#208 병합)의 빌드다. 수동 실행한 release run [44753895153](https://github.com/handochan/aelix-analytics/actions/runs/44753895153)이
만들었고 `docs/releases/0.32.1.sha256`(#213, `84433ab`)이 기록한다.

| 파일 | sha256 |
|---|---|
| `aelix_analytics-0.32.1-py3-none-any.whl` | `878741132f704146ecae77a1ca6bb83d63fefe18a8ec99ce43b5a87dcdbb304e` |
| `aelix_analytics-0.32.1.tar.gz` | `2f6f1486346743c54126f607e78f2ba97be00a8bdc71dfe7f44aa86d11cef5a4` |

측정은 네 경로로 후보에 닿는다.

1. **설치본.** run 44753895153의 `verify`가 그 두 파일을 세 OS에서 설치·업그레이드·제거했다. Linux 61 passed, macOS 61 passed,
   Windows 60 passed·1 skipped(대역 uv가 스크립트라 Windows가 실행하지 못하는 시험). 그 run의 artifact에서 내려받은 두 파일의 sha256은 위와 같다.
2. **저장소 시험.** main `494d1ce`의 CI [44738231086](https://github.com/handochan/aelix-analytics/actions/runs/44738231086). 빌드가 재현되므로
   체크아웃의 `src/`가 후보 wheel의 kit과 같은 바이트다. `84433ab`의 CI [44762594792](https://github.com/handochan/aelix-analytics/actions/runs/44762594792)도 성공했다.
3. **측정 도구.** 도구는 clean detached `84433ab`에서 돌렸다. `494d1ce`와는 `docs/releases/0.32.1.sha256`만 다르다. fixture도 거기서 만들었다(manifest의 `kit_commit`).
4. **로컬 전체 시험.** `84433ab`에서 후보 파일을 `AELIX_ANALYTICS_TEST_DIST`로 주고 돌렸다(아래).

기기: macOS 26.6.2 arm64(18코어, 64 GB), 로컬 SSD, CPython 3.12.13, SQLite 3.53.1, Chromium 156.0.8078.4(Playwright 1.64.0).
**이번 측정 동안 기기에 다른 작업의 부하가 있었다**(load average 약 10–17, 편집기 확장과 다른 Python 프로세스). 그래서 Q9의 시간은 0.32.0보다
길게 나왔다. 같은 부하에서 0.32.0 kit과 번갈아 잰 비교를 함께 적는다(아래 Q9).

| 무엇 | 기준 | 결과 |
|---|---|---|
| main CI 44738231086 | `494d1ce` | 성공(필수 검사 7개). 전체 시험 Linux 3,471 passed·114 skipped, macOS 3,472 passed·113 skipped, Windows 3,267 passed·350 skipped. aelix 0.1.0b2 통합 3.11·3.12 각 1,118 passed(Windows는 각 1 skipped). 런처 CPython 3.11: Linux·macOS 220 passed·1 skipped, Windows 103 passed·118 skipped. Linux Chromium 13 passed. Windows 콘솔 21 passed, LongPathsEnabled=0 7 passed. usage 보고 필수 사실 58/58, 불변식 42/42(세 OS) |
| 로컬 전체 시험(`AELIX_ANALYTICS_TEST_INSTALL=1`, `AELIX_ANALYTICS_TEST_DIST`=후보, `AELIX_ANALYTICS_TEST_BROWSER=chromium`) | `84433ab` | 3,487 passed, 130 skipped, 1,622초(부하 아래). skip은 Windows 전용(Windows 구현의 계약 시험, 콘솔, 긴 경로), aelix 레그 밖의 `aelix_coding_agent`, 이 기기에 없는 파일 시스템, M fixture를 두 번 쓰는 선택 시험(`test_eval_fixtures`), 기록용 대역이 선언하지 않는 계약이다 |

## 게이트별 결과

| Gate | 결과 | 증거 | 제한 |
|---|---|---|---|
| Q1 정체성·배포 | pass | 위 1(세 OS의 설치·업그레이드·제거, aelix 0.1.0b2에 실제 결합한 endpoint와 manifest). 위 2의 명칭·영어 기본값(`test_language`, `test_workspace_i18n`), 문서 검사(`test_doc_checks`), 영어 README와 한국어 README의 wheel 설명(`test_packaging`·`test_release_check`, #209). [지원 범위](../SUPPORT.md)를 이 후보로 다시 채움 | PyPI에서 이름으로 설치는 게시 뒤(릴리스 절차 6단계). 설치 시험의 색인은 PyPI 자리의 loopback 색인 |
| Q4 근거 정체성 | pass | 위 2의 `test_relations`, `test_record_contract`, `test_run_views`, `test_workspace_relations`. S·M fixture의 사례 판정을 두 형식에서 `check`로 다시 계산: 4개 모두 `problems` 0 | — |
| Q7 기록 호환성 | pass | 위 2의 `test_marker_gate`, `test_migration`, `test_migration_reader`, `test_migrate_command`, `test_writer`, `test_new_format`, `test_journal_append`. S·M `v1` 사본의 명시적 전환(아래), 원본 트리 해시 불변 | — |
| Q8 실패의 가시성 | pass | 위 2의 `test_kit`, `test_writer`, `test_platform`, `test_reports`, `test_input_caps`, `test_metrics`, `test_result_line`, `test_observed`(관찰의 제한 시간·상한에서 꺼짐을 알림, #171) | — |
| Q9 규모와 비용 | pass(측정 범위) | 아래 Q9. M의 warm 제한 조회 p95 최대 445 ms(목표 500 ms). 빌드된 첫 화면을 여는 load p95 54 ms(목표 2초). 인덱스 손상 뒤 재구축과 원본 응답. 조회 중 변경의 갱신 상태는 위 2의 `test_retrieval`·`test_relations`·`test_workspace_relations` | 부하가 있는 macOS 한 기기. 읽은 파일량, L·Linux·Windows는 미측정. M에서 run 끝의 전체 빌드 9.9–13.2초(p50, 부하 아래) |
| Q10 분석·표시 경계 | 표시 경계 pass. 분석 검사는 판정 없음(T07–T09는 모델 기록의 R5) | 표시: S·M의 `display_safety` 사례(마크업·경로·템플릿처럼 보이는 문장)를 HTML 파서와 Chromium으로 따로 판정했다. 문장 전체가 글자로 든 페이지 S 4, M 3이고, 그 일부로 만들어진 요소·`javascript:` 링크·주석은 0이다. 새 표시 표면(관찰의 가린 명령·경로·스크립트 사본 #171, 긴 글 자르기 #184, 새 탭 #168)은 위 2의 `test_relation_screens`, `test_observed_screens`, `test_workspace_*`. 분석 검사 예제: `q10` fixture 두 형식의 `check` 통과 | 공유 범위는 #20·#21의 기록 |
| Q12 지표와 분석 방향 | 결정적 사례 pass | 위 2의 `test_metrics`, `test_checkpoints` | 새 세션의 재개는 [모델 기록](2026-11-21-issue-24-product-eval.md)의 C3 |
| Q15 장기 확장 구조 | pass | 위 2의 `test_layers`(역방향 import), `test_contracts`, `test_asset_contract`, `test_extension`·`test_asset_host`. stdlib만의 CPython 3.11(위 main CI의 `floor` 세 OS: 위반 0, Windows는 선언된 한계 1), aelix 통합(3.11·3.12) | — |
| Q16 보고서 산출물 | pass | `tools/report_storage_eval.py`·`tools/report_collection_eval.py`를 clean `84433ab`에서 다시 돌림: 모든 단언 true(아래). 위 2의 `test_reports`, `test_report_metadata`, `test_report_renderers`, `test_workspace_reports`, `test_publication_reports` | 사람이 본 Reports 탐색은 Q6 |
| Q17-a 기능 | pass(5단계 Workspace 이동은 미측정) | 아래 Q17-a. 후보 wheel을 새 HOME에 설치, sandbox에서 세션 6개, 모델 호출 0 | 0.32.0 기록과 같은 측정 범위(`not_covered`) |

Q2·Q3(토큰)·Q12의 새 세션·Q13은 [모델 기록](2026-11-21-issue-24-product-eval.md), Q5·Q11은 [그 기록](2026-11-21-issue-24-q5-q11.md)에 있다.
Q6·Q14의 사람 몫은 첫 베타 뒤로 미뤘다(2026-11-04 소유자).

## Q9: Workspace와 제한 조회

fixture는 `tools/eval_fixtures.py scale --seed 0`의 S(30 runs, 300 events, 5 datasets)와 M(300 runs, 10,000 events, 20 datasets)이다.
`v2`는 이 kit의 형식이고 `v1`은 `V1_KIT_COMMIT`(kit 0.7.0)이 썼다. 측정은 모두 사본에서 했다.

**Workspace**(`tools/workspace_scale_eval.py`, `v2`, warm·변경 없음 10회, cold 3회). 정의는 0.32.0 기록과 같다.

| | S | M |
|---|---|---|
| cold 빌드 p50 / p95 | 735 / 824 ms | 13,346 / 13,845 ms |
| warm 빌드 p50 / p95 | 679 / 734 ms | 13,051 / 14,340 ms |
| 변경 없음 확인(`render --if-changed`) p50 / p95 (프로세스 전체 p95) | 19.7 / 21.9 ms (115 ms) | 114 / 122 ms (181 ms) |
| 최대 메모리 cold / warm / 변경 없음 | 130 / 111 / 32 MiB | 661 / 362 / 77 MiB |
| 페이지 / 전체 크기 | 511쪽 / 15.2 MB | 12,266쪽 / 293.3 MB |
| 첫 화면 `index.html` | 336 KB | 463 KB |
| Chromium `file://` load p50 / p95 | 66.3 / 111.9 ms | 51.8 / 53.9 ms |
| 페이지 오류·dialog | 0 | 0 |

- **같은 부하에서 0.32.0 kit과 번갈아 잼**(같은 하네스 도구, 0.32.0 kit은 clean `26c28d0`).
  - M warm 빌드 p50: 0.32.1 13.2초·9.9초, 0.32.0 10.7초·10.0초다. 변경 없음 확인 p95는 0.32.1 168·154 ms, 0.32.0 136·143 ms다.
  - S warm 빌드 p50: 0.32.1 479·433 ms, 0.32.0 479·493 ms다.
  - 차이는 부하의 흔들림 안이다. 0.32.0 기록(M warm 6.3초)과의 차이는 기기 부하로 본다. 이 kit 때문이라는 근거는 찾지 못했다.
- **크기가 늘었다.** 같은 M fixture에서 0.32.0 kit은 250.3 MB, 0.32.1 kit은 293.3 MB(+17%)를 쓴다. S는 13.4 → 15.2 MB다. 페이지 수는 같다.
  쪽마다 Aelix 마크의 data URI(약 1.7 KB)가 두 번(탭 아이콘과 바닥글) 들어가서다([#204](https://github.com/handochan/aelix-analytics/issues/204)).
  첫 화면은 450 → 463 KB(+12 KB)다. 첫 화면과 기본 응답이 기록량에 비례해 커지지 않는다는 Q9의 기준은 그대로다. 전체 Workspace는 기록에 비례해 커진다.

**제한 조회**(`tools/retrieval_eval.py`, warm 10회, 첫 쪽 8개). `v1`은 명시적으로 전환한 사본, `v2`는 사본 그대로다.

| | S `v2` | S `v1` 전환 | M `v2` | M `v1` 전환 |
|---|---|---|---|---|
| warm p95, 네 과제 중 최대 | 18.1 ms | 18.6 ms | 445.2 ms | 322.7 ms |
| cold(인덱스 없음) 최대 | 21.1 ms | 19.3 ms | 636.3 ms | 421.4 ms |
| 인덱스 손상 뒤 재구축 최대 | 19.5 ms | 17.8 ms | 599.6 ms | 439.6 ms |
| 응답 최대 | 9,553 B | 10,725 B | 8,790 B | 9,773 B |
| 전체 쪽 회수율 | 1.0 | 1.0 | 1.0 | 1.0 |

- 네 과제는 오래된 발견, 정정, 철회, 긴 한국어다. 재구축은 인덱스의 손상을 알아채고(`corrupt`) 다시 만든 뒤 원본에서 답했다. 원본 journal의 해시는 측정 전후로 같다.
- **같은 부하에서 0.32.0 kit과 번갈아 잼(M `v2`)**: warm p95 최대가 0.32.1 363–442 ms(다섯 번), 0.32.0 296–468 ms(세 번)다.
  두 kit이 저마다 쓴 fixture에서 쟀다(0.32.0 kit은 0.32.1이 쓴 사본을 그 kit이 쓴 것이 아니라며 받지 않는다).
  - 같은 질의(긴 한국어)를 두 kit에서 cProfile로 재면 함수 호출 수(약 1,048만)와 CPU 시간(5회 2.29초 · 2.27초)이 같다.
  - 그래서 조회의 차이도 부하의 흔들림으로 본다. 0.32.0 기록은 M warm p95 286.5 ms였다.
  - cold는 이번에 목표의 범위(500 ms)를 넘기도 했다(636 ms). 목표는 warm에 두었다.

**새 비용**(모델 없음, aelix 0.1.0b2의 확장 hook을 그대로 부름).

| | 결과 | 비교 |
|---|---|---|
| #169 프로젝트 찾기(작업 폴더 아래, 깊이 3, 디렉터리 2,000개 상한) | 3×3×3 p95 19 ms. 12×12×12(1,884개) p95 74 ms. 상한을 넘는 15×15×15(3,615개)는 p95 100 ms에 멈추고 `complete: false`. 실제 저장소 루트 p95 4 ms | — |
| #171 bash 한 번의 관찰(앞과 뒤를 더한 지연) | 파일 1,000개 p50 118 ms, 10,000개 p50 206 ms(최대 220), 50,000개는 상한으로 꺼짐(`not_observed`·`too_many_entries`, 그 뒤 `observation_off`). 64 MiB·200 MiB 파일의 해시 117·176 ms | [#171 기록](2026-11-10-issue-171-observed-activity.md): 1만 개 약 125 ms(부하 없음) |

## Q7: 명시적 전환

`v1` fixture 사본을 `python -m aelix_analytics --project <사본> migrate --to <새 폴더>`로 옮겼다(`/usr/bin/time -l`).

| | 시간 | 최대 메모리 | 복사한 파일 / 바이트 | 제외 / 복사 못 함 | 원본 트리 |
|---|---|---|---|---|---|
| S | 1.3초 | 35 MiB | 228 / 2.9 MB | 5 / 0 | 같음(파일 231개의 해시) |
| M | 10.9초 | 76 MiB | 2,036 / 223.1 MB | 5 / 0 | 같음(파일 2,039개의 해시) |

## Q16: 보고서 도구

| 도구 | 단언 |
|---|---|
| `report_storage_eval.py` | 같은 요청은 기존 버전, v2·상태 갱신 뒤에도 v1의 해시가 같음 |
| `report_collection_eval.py` | 부분 성공의 개수, 실패분을 더한 새 묶음, 같은 요청은 기존 것, 렌더 반복이 같은 바이트, v1의 바이트·metadata·구성원 불변 |

## Q17-a: 모델 없이 사람만의 분석

`tools/human_e2e.py prepare`는 clean 체크아웃의 wheel을 만들어 새 HOME에 설치한다. 체크아웃은 clean detached `84433ab`이었다. 그 wheel은 후보와
같은 바이트다(`878741132f704146…`). 도구는 0.32.0 때와 같다(`26c28d0` 뒤로 바뀌지 않음). 리허설 스크립트 여섯 개도 0.32.0 때와 바이트까지 같다.

- 설치: `uv tool install`, uv 0.11.19, uv 관리 CPython 3.12.13. 분석 라이브러리: duckdb 1.5.6, polars 1.44.2, pyarrow 26.0.1, matplotlib 3.11.2, plotly 6.9.0, Pillow 13.0.0.
- 데이터: `data/orders.csv`(seed 23, 856행). sha256이 #23·0.32.0과 같다.
- 세션 6개(만들기, 맥락·가져오기·SQL·스크립트, 기록·같은 일수 비교·Report 시도, Visualization 참조·Report v1·v2·렌더, 새 프로세스의 status·context·checkpoint,
  Report v1의 파일 대조)가 모두 종료 코드 0으로 끝났다.
- **모델 호출 0.** `check --project <OUT>/work/q3-sales`가 prepare부터 판정 시점까지 막힌 시도를 다시 읽었고 0이었다(`passed: true`).
  막는 범위는 #23·0.32.0과 같다: 외부 IP와 Unix 소켓 연결, PATH의 모델 실행기, 그 설정·로그인·모델 저장소의 읽기.
- **출력이 0.32.0과 같다.** 여섯 세션의 출력을 0.32.0의 출력과 비교했다. run·Report ID, 시각, 경로, 시간 값을 가리면 다른 줄은 다음뿐이다.
  - 상태·맥락의 머리 줄이 `report:`에서 `workspace:`로 바뀌었다([#161](https://github.com/handochan/aelix-analytics/issues/161), CHANGELOG 0.32.1).
    렌더 안내의 문구도 그 변경을 따랐다.
  - 환경 줄의 pyarrow 26.0.0 → 26.0.1, numpy 2.5.4 → 2.5.5(prepare가 고른 판).
  - 일부러 틀린 첫 run(`ColumnNotFoundError`)과 고친 run, 스크립트가 틀린 입력을 준 곳(`report_input_invalid`, `name_not_portable`)과
    출력 도우미의 traceback(세션 2의 2건, 세션 3의 1건)은 0.32.0과 같다.
- 마지막 세션에서 v2 뒤의 v1이 그대로였다: `recording_status` complete, `content` resolved, `state` current.

## 실행하지 않은 것

- 읽은 파일량(Q9): 표준 라이브러리에 이식 가능한 계측이 없다. Linux·Windows에서의 Q9 수치, L 규모. 부하가 없는 기기에서의 다시 잼.
- Q17-a의 5단계 Workspace 이동과 사람의 사용성(소유자 결정으로 갈음), Q17-b(kit 0.25.0 기록 그대로).

## 명령

```sh
git worktree add --detach <측정> 84433ab
# <측정>에서. AELIX_ANALYTICS_TRUST_FILE은 임시 파일
uv run --locked python tools/eval_fixtures.py scale --scale S --seed 0 --format v2 --out <S-v2>    # M, v1(--kit-root <v1 kit>/src)도
uv run --locked python tools/eval_fixtures.py check <fixture>
uv run --locked python tools/eval_fixtures.py q10 --seed 0 --format v2 --out <q10-v2>             # v1도
uv run --locked python tools/report_storage_eval.py --out <dir>
uv run --locked python tools/report_collection_eval.py --out <dir>
uv run --locked --group browser python tools/workspace_scale_eval.py --fixture <S-v2> --project <사본> --out <dir> --repeats 10 --cold-repeats 3 --browser chromium
uv run --locked python tools/retrieval_eval.py --fixture <fixture> --project <사본 또는 전환한 사본> --out <dir> --repeats 10
PYTHONPATH=<측정>/src /usr/bin/time -l <측정>/.venv/bin/python -m aelix_analytics --project <v1 사본> migrate --to <새 폴더>
uv run --locked python tools/human_e2e.py prepare <OUT> --checkout <측정>
uv run --locked python tools/human_e2e.py shell <OUT> --script <rehearse-a1.zsh>   # a2–a5, verify-v1도 하나씩
uv run --locked python tools/human_e2e.py check <OUT> --project <OUT>/work/q3-sales
```
