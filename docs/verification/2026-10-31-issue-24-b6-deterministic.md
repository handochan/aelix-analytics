# Issue #24 B6 — 결정적 게이트를 출시 후보 0.32.0으로 (2026-10-31)

[Issue #24](https://github.com/handochan/aelix-analytics/issues/24)의 출시 리허설(B6) 가운데 모델이 필요 없는 게이트를 출시 후보로 잰 기록이다.
[릴리스 절차](../../CONTRIBUTING.md#릴리스-절차)의 3단계다. 수치·명령·해시의 정본은 [JSON](2026-10-31-issue-24-b6-deterministic.json)이다.
fixture와 그 사본, Workspace, 원시 측정 디렉터리는 이 기기에만 두고 커밋하지 않았다.

## 후보와 측정의 연결

후보는 main `26c28d0`(#142 병합)의 빌드다. 수동 실행한 release run [41150700659](https://github.com/handochan/aelix-analytics/actions/runs/41150700659)가
만들었고, `docs/releases/0.32.0.sha256`(#143)이 기록한다.

| 파일 | sha256 |
|---|---|
| `aelix_analytics-0.32.0-py3-none-any.whl` | `203f7e10583a51e30dd361c2d8e038c73428ac95b1789ee09e8ece4a7a437440` |
| `aelix_analytics-0.32.0.tar.gz` | `b243bc417675205a2360c7ab3f35b5be7a67f10237a8a16d63fd47ae88ea0add` |

측정은 네 경로로 후보에 닿는다.

1. **설치본.** run 41150700659의 `verify`가 그 두 파일을 세 OS에서 설치·업그레이드·제거했다(`tests/test_packaging.py`, `tests/test_release_check.py`).
   Linux 58 passed, macOS 58 passed, Windows 57 passed·1 skipped(대역 uv가 스크립트라 Windows가 실행하지 못하는 시험). 그 가운데
   `test_the_given_files_are_this_checkout_s_build`가 그 파일이 `26c28d0`의 빌드임을 확인했다.
2. **저장소 시험.** main `26c28d0`의 CI [41150692492](https://github.com/handochan/aelix-analytics/actions/runs/41150692492). 빌드가 재현되므로
   체크아웃의 `src/`가 후보 wheel의 kit과 같은 바이트다.
3. **측정 도구.** 이 PR의 도구·시험은 배포본 밖이다. 도구를 돌린 커밋(제한 조회·전환 `056760d`, 리뷰 뒤 다시 잰 Workspace `80a8510`)마다
   `git diff --quiet 26c28d0 HEAD -- src extension README.md pyproject.toml LICENSE .gitignore`가 같음을 확인했다. fixture는 clean detached
   `26c28d0`에서 만들었다(manifest의 `kit_commit`).
4. **로컬 전체 시험.** `056760d`에서 후보 파일을 `AELIX_ANALYTICS_TEST_DIST`로 주고 돌렸다(아래).

기기: macOS 26.6.2 arm64(18코어, 64 GB), 로컬 SSD, CPython 3.12.13, SQLite 3.53.1, Chromium 156.0.8078.4(Playwright 1.64.0). 기기 기록은
`tools/eval_fixtures.py environment` 형식이다.

| 무엇 | 기준 | 결과 |
|---|---|---|
| main CI 41150692492 | `26c28d0` | 성공(필수 검사 7개). 전체 시험 Linux 3119 passed·112 skipped, macOS 3120 passed·111 skipped, Windows 2922 passed·340 skipped. aelix 0.1.0b2 통합 3.11·3.12 각 997 passed(Windows는 각 1 skipped). stdlib만의 CPython 3.11: Linux·macOS 208 passed·1 skipped, Windows 97 passed·112 skipped. Linux Chromium 13 passed. usage 보고 필수 사실 58/58, 불변식 42/42 |
| 로컬 전체 시험(`AELIX_ANALYTICS_TEST_INSTALL=1`, `AELIX_ANALYTICS_TEST_DIST`=후보, `AELIX_ANALYTICS_TEST_BROWSER=chromium`) | `056760d` | 3140 passed, 129 skipped, 815초. skip은 Windows 전용(콘솔·긴 경로), aelix 레그 밖의 `aelix_coding_agent`, 이 기기에 없는 파일 시스템. 명령의 `--ignore=tests/test_console.py`는 없는 경로라 아무것도 빼지 않았다 |
| 리뷰 수정 뒤 바뀐 시험(`tests/test_workspace_scale_eval.py`, Q8·Q16 시험, Chromium 포함) | `80a8510` | 6 passed |

## 게이트별 결과

| Gate | 결과 | 증거 | 제한 |
|---|---|---|---|
| Q1 정체성·배포 | pass(지원표 제외) | 위 1(세 OS의 설치·업그레이드·제거, aelix 0.1.0b2에 실제 결합한 endpoint와 manifest: `test_aelix_binds_and_loads_the_installed_extension`). 위 2의 명칭·영어 기본값(`test_language`, `test_workspace_i18n`), 문서 검사(`test_doc_checks`), `tools/release_check.py version` | 후보로 다시 채운 [지원 범위](../SUPPORT.md)가 남았다(#24 PR-4, BETA §2 Q1 문단). PyPI에서 이름으로 설치하는 것은 게시 뒤(릴리스 절차 6단계). 설치 시험의 색인은 PyPI 자리의 loopback 색인 |
| Q4 근거 정체성 | pass | 위 2의 `test_relations`(정답 관계 fixture 여섯 사례, 같은 chart ID·미고정·복수 생산자·미보존·순환·옛 kit 추정), `test_record_contract`, `test_run_views`, `test_workspace_relations`. S·M fixture의 사례 판정을 두 형식에서 `check`로 다시 계산: 4개 모두 `problems` 0 | — |
| Q7 기록 호환성 | pass | 위 2의 `test_marker_gate`, `test_migration`, `test_migration_reader`, `test_migrate_command`, `test_writer`(구버전 writer 차단), `test_new_format`, `test_journal_append`. S·M `v1` fixture 사본의 명시적 전환(아래), 원본 트리 해시 불변 | — |
| Q8 실패의 가시성 | pass | 위 2의 `test_kit`(오류·제한 시간·finalize), `test_writer`(완료 전 종료), `test_platform`(취소·제한 시간), `test_reports`(등록 실패·재시도), `test_input_caps`, `test_metrics`, `test_result_line`. 새 시험: run 끝의 Workspace 빌드 실패가 run의 `workspace_error`로 보이고 run 기록·출력·journal 줄은 남음(`test_a_workspace_that_cannot_be_built_after_a_run_is_told_and_the_run_kept`) | — |
| Q9 규모와 비용 | pass(측정 범위) | 아래 Q9. M의 warm 제한 조회 p95 287 ms 이하(목표 500 ms). 빌드된 첫 화면을 여는 load p95 56 ms(목표 2초). aelix 확장이 프롬프트마다 띄우는 변경 없음 확인 p95 98 ms. 인덱스 손상 뒤 재구축과 원본 응답. 조회 중 변경의 갱신 상태와 제한 응답은 위 2의 `test_retrieval`(`test_repeated_source_changes_return_updating_without_facts` 등)·`test_relations`·`test_workspace_relations` | macOS 한 기기. 읽은 파일량은 미측정. L·Linux·Windows는 미측정. run이 끝날 때마다 Workspace 전체를 다시 쓰고 M에서 warm 6.3초(p50) |
| Q10 분석·표시 경계 | 표시 경계 pass. 분석 검사 not run | 표시: S·M의 `display_safety` 사례(마크업·경로·템플릿처럼 보이는 문장)를 두 번 따로 판정했다. HTML 파서로 모든 페이지를 읽어 문장 전체가 글자로 든 페이지가 S 4, M 3이고, 그 일부로 만들어진 요소·`javascript:` 링크·주석은 0이다. Chromium에서도 그 페이지들의 문서 글자에 문장 전체가 있고 만들어진 것·dialog는 0이다. 위 2의 `test_relation_screens`, `test_report_renderers`, `test_workspace_reports`. 분석 검사 예제: `q10` fixture 두 형식의 `check` 통과(네 검사 × 결함/바른 분석 한 쌍) | 예제의 판정은 모델 과제(T06–T09)라 PR-3. 공유 범위는 #20·#21의 기록 |
| Q12 지표와 분석 방향 | 결정적 사례 pass. 새 세션 재개 not run | 위 2의 `test_metrics`, `test_checkpoints`(사례의 대응은 [#14 기록](2026-10-09-issue-14-metrics.md)과 [#16 기록](2026-10-11-issue-16-checkpoints.md)) | 새 세션의 재개는 모델 과제(PR-3) |
| Q15 장기 확장 구조 | pass | 위 2의 `test_layers`(역방향 import), `test_contracts`(플랫폼 포트 세 구현, `PublicationProjection`(#20)·`ReportRenderer`(#19) 교체), `test_asset_contract`(`ProposalPolicy` 교체), `test_extension`·`test_asset_host`(프로젝트·세션 격리). stdlib만의 CPython 3.11(위 main CI), aelix 통합(3.11·3.12 각 997 passed) | — |
| Q16 보고서 산출물 | pass | `tools/report_storage_eval.py`·`tools/report_collection_eval.py`를 clean `26c28d0`에서 다시 돌림: 모든 단언 true(아래). 위 2의 `test_reports`, `test_report_metadata`, `test_report_renderers`, `test_workspace_reports`, `test_publication_reports`. 새 시험: Report의 본문·파일(base64 포함)이 매 턴의 context block과 status에 없음. kit에는 모델·HTTP client가 없다(`src/`·`extension/`의 import. `socket`은 `gethostname`만 쓴다. 환경을 만드는 `uv sync`는 별도 프로세스) | 사람이 본 Reports 탐색은 Q6 |

Q2·Q3(토큰)·Q5·Q11·Q13은 모델 캠페인(PR-3), Q6·Q14의 사람 몫은 소유자가 한다. Q17-a는 kit 0.25.0으로 잰 기록이 있고, 후보로 다시 잰 결과는 따로 기록한다.

## Q9: Workspace와 제한 조회

fixture는 `tools/eval_fixtures.py scale --seed 0`의 S(30 runs, 300 events, 5 datasets)와 M(300 runs, 10,000 events, 20 datasets, 원본 217 MB)이다.
`v2`는 이 kit의 형식이고 `v1`은 `V1_KIT_COMMIT`(kit 0.7.0)이 썼다. 측정은 모두 사본에서 했다.

**Workspace**(`tools/workspace_scale_eval.py` `80a8510`, `v2`, warm·변경 없음 10회, cold 3회. 리뷰 전 도구 `056760d`로 처음 잰 값도 같은 범위였다). cold는 kit의 해시 캐시와 Workspace를 비운 빌드이고 OS 파일
캐시는 비우지 않았다. 빌드 시간은 자식 프로세스 안의 `workspace.build`이고, `analytics render`의 `uv run`은 더하지 않았다.
run이 끝날 때와 기록·가져오기 뒤에는 전체 빌드(warm)를 하고, aelix 확장은 프롬프트마다 `render --if-changed`(변경 없음 확인)를 따로 띄운다.
`analytics open`은 있는 첫 화면을 연다(아래 Chromium load).

| | S | M |
|---|---|---|
| cold 빌드 p50 / p95 | 336 / 357 ms | 6,222 / 6,512 ms |
| warm 빌드 p50 / p95 | 313 / 324 ms | 6,259 / 6,489 ms |
| 변경 없음 확인(`render --if-changed`) p50 / p95 (프로세스 전체 p95) | 10.5 / 11.1 ms (57 ms) | 88 / 98 ms (145 ms) |
| 최대 메모리 cold / warm / 변경 없음 | 126 / 107 / 31 MiB | 595 / 353 / 76 MiB |
| 페이지 / 전체 크기 | 511쪽 / 13.4 MB | 12,266쪽 / 250.4 MB |
| 첫 화면 `index.html` | 324 KB | 450 KB |
| Chromium `file://` load p50 / p95 | 41.6 / 43.5 ms | 45.8 / 56.2 ms |
| 페이지 오류·dialog | 0 | 0 |

첫 화면은 기록이 33배일 때 1.4배다. 전체 Workspace는 기록에 비례해 커진다(run·dataset·관계마다 한 쪽).

**제한 조회**(`tools/retrieval_eval.py`, warm 10회, 첫 쪽 8개). `v1`은 명시적으로 전환한 사본(#15의 방법), `v2`는 사본 그대로다.

| | S `v2` | S `v1` 전환 | M `v2` | M `v1` 전환 |
|---|---|---|---|---|
| warm p95, 네 과제 중 최대 | 11.9 ms | 11.3 ms | 286.5 ms | 211.6 ms |
| cold(인덱스 없음) 최대 | 14.4 ms | 11.8 ms | 450.7 ms | 278.6 ms |
| 인덱스 손상 뒤 재구축 최대 | 13.1 ms | 10.7 ms | 403.9 ms | 285.2 ms |
| 응답 최대 | 9,553 B | 10,725 B | 8,790 B | 9,773 B |
| 전체 쪽 회수율 | 1.0 | 1.0 | 1.0 | 1.0 |

- 네 과제는 오래된 발견, 정정, 철회, 긴 한국어다. 재구축은 인덱스의 손상을 알아채고(`corrupt`) 다시 만든 뒤 원본에서 답했다.
- 첫 쪽에 정답이 없는 과제: 긴 한국어(S·M)와 M의 오래된 발견. 다음 쪽을 따라가면 모두 찾는다(M의 오래된 발견은 7쪽, 645개 가운데).
  이 차이는 질문을 고르는 모델이 다음 쪽을 읽는지에 달려 있어 PR-3의 과제에서 본다.
- 원본 journal의 해시는 측정 전후로 같다.

## Q7: 명시적 전환

`v1` fixture 사본을 `python -m aelix_analytics --project <사본> migrate --to <새 폴더>`로 옮겼다(`/usr/bin/time -l`).

| | 시간 | 최대 메모리 | 복사한 파일 / 바이트 | 제외 / 복사 못 함 | 원본 트리 |
|---|---|---|---|---|---|
| S | 1.1초 | 32 MiB | 228 / 2.9 MB | 5 / 0 | 같음 |
| M | 9.1초 | 74 MiB | 2,036 / 223.1 MB | 5 / 0 | 같음 |

## Q16: 보고서 도구

| 도구 | 단언 |
|---|---|
| `report_storage_eval.py` | 같은 요청은 기존 버전, v2·상태 갱신 뒤에도 v1의 해시가 같음 |
| `report_collection_eval.py` | 부분 성공의 개수, 실패분을 더한 새 묶음, 같은 요청은 기존 것, 렌더 반복이 같은 바이트, v1의 바이트·metadata·구성원 불변 |

## 실행하지 않은 것

- 읽은 파일량(Q9): 표준 라이브러리에 이식 가능한 계측이 없다.
- Linux·Windows에서의 Q9 수치, L 규모.
- 분석 검사 예제의 판정(Q10), 새 세션 재개(Q12): 모델 캠페인(PR-3).
- Q17-a의 후보 재측정: `tools/human_e2e.py`로 따로 기록한다. kit 0.25.0 기록 뒤 kit이 바뀌었다(0.25.0 → 0.32.0, `src`·`extension`의 30개 파일).

## 명령

```sh
git worktree add --detach <측정> 26c28d0
git worktree add --detach <v1 kit> ebd10b644e8cb8550ccf6446b134666db6bb9386
# <측정>에서. AELIX_ANALYTICS_TRUST_FILE은 임시 파일
uv run --locked python tools/eval_fixtures.py scale --scale S --seed 0 --format v2 --out <S-v2>    # M, v1(--kit-root <v1 kit>/src)도
uv run --locked python tools/eval_fixtures.py check <fixture>
uv run --locked python tools/eval_fixtures.py q10 --seed 0 --format v2 --out <q10-v2>             # v1도
uv run --locked python tools/report_storage_eval.py --out <dir>
uv run --locked python tools/report_collection_eval.py --out <dir>
# 이 PR의 커밋에서, 사본마다
uv run --locked --group browser python tools/workspace_scale_eval.py --fixture <S-v2> --project <사본> --out <dir> --repeats 10 --cold-repeats 3
uv run --locked python tools/retrieval_eval.py --fixture <fixture> --project <사본 또는 전환한 사본> --out <dir> --repeats 10
PYTHONPATH=<측정>/src /usr/bin/time -l <측정>/.venv/bin/python -m aelix_analytics --project <v1 사본> migrate --to <새 폴더>
```
