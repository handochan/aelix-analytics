# Issue #20 PR3 — build 결박과 Workspace 전달 관찰

[Issue #20](https://github.com/handochan/aelix-analytics/issues/20)·[Draft PR #120](https://github.com/handochan/aelix-analytics/pull/120),
[ADR-0014](../decisions/0014-publication-record-contract.md)의 PR3 구현을 측정했다. 수치·개별 실패·명령·소스 해시는 [JSON](2026-10-22-issue-20-publication-build-pr3.json)에 있다.

## 소스와 실행 기준

clean commit `d6281e5b005ed14fe145d9ecb9e0932217ff0219`(kit 0.26.0, reader format 6)에서 실행했다.
이전 작업 파일을 `ef0b44b`·`184910c` 두 부모의 `5f36f4d`로 보존하고, main `5559ff6`(#132)을 일반 merge한 커밋이다.
측정·전체 시험·Ruff·문서 검사 전후 tracked 소스 해시가 같고 작업 트리는 clean이었다. 이 기록을 반영하는 뒤 커밋은 측정 문서만 바꾼다.
이전 dirty 예비 측정과 실패 로그는 이전 run 및 이 문서의 이전 Git 리비전에 보존한다.
[2026-10-19 backend 측정](2026-10-19-issue-20-publication-build-pr3.md)은 당시 범위의 증거다.

## 측정 범위와 결과

작은 합성 프로젝트에서 read/evidence 각각 두 build, preview 무쓰기, 새 정체성과 같은 내용, 결박·기준·제외 canary·결정적 ZIP을 확인했다.
Workspace Timeline·참조 관계 페이지는 폴더가 `same`·`changed`·`missing`일 때도 기록은 `complete`, 참조는 `resolved`로 구별한다.
화면을 만드는 동안 journal이 변하지 않음과 복구한 폴더 검사까지 **34 assertions PASS**다.

| 프로필·시도 | 파일 | 패키지 bytes | manifest bytes | 이벤트 bytes(LF 포함) |
|---|---:|---:|---:|---:|
| read · 1 | 10 | 16,796 | 2,870 | 1,366 |
| read · 2 | 10 | 16,796 | 2,870 | 1,366 |
| evidence · 1 | 10 | 16,899 | 2,874 | 1,374 |
| evidence · 2 | 10 | 16,899 | 2,874 | 1,374 |

이 fixture에는 run/figure가 없다. 그 바이트·형식·출처는 실제 runner 회귀시험의 범위로 구별한다.
관측 시간은 성능 게이트·M 규모·사용자 프로젝트 수용이 아니며 사용자 데이터·실제 모델·토큰은 쓰지 않았다.

## 전체 시험과 환경 한계

임시 `AELIX_ANALYTICS_TRUST_FILE`을 지정하고 `AELIX_LAB_HOME`·`AELIX_LAB_TRUST_FILE`을 제거했다.
명령은 `AELIX_ANALYTICS_TEST_INSTALL=1 uv run --locked pytest -q -p no:cacheprovider --timeout 600 --timeout-method=thread --ignore tests/test_windows_console.py`다.
결과는 **2840 PASS / 13 FAIL / 89 SKIP**(1652.33초), exit `1`다.
Ruff와 문서 검사도 통과했다. 전체 시험의 실패를 통과로 바꾸지 않으며 시험을 약화하거나 추가 skip을 넣지 않았다.

이 Codex 세션 안에서는 nested `sandbox-exec` 최소 명령도 `sandbox_apply: Operation not permitted`(exit71)로 거부되고,
`ps` 시작은 `PermissionError: [Errno 1] Operation not permitted`로 거부된다. 개별 실패와 원인은 다음과 같다.

| 실패 시험 | 이 세션의 원인 |
|---|---|
| `tests/test_human_e2e.py::test_only_what_the_session_started_is_waited_for_or_stopped` | Descendants.start()의 ps 실행이 PermissionError errno1로 거부되어 자식 관찰을 시작하지 못함. |
| `tests/test_human_e2e.py::test_an_orphan_of_the_same_terminal_is_never_touched` | Descendants.start()의 ps 실행이 PermissionError errno1로 거부되어 orphan 경계 시험을 시작하지 못함. |
| `tests/test_human_e2e.py::test_the_process_table_reads_the_same_in_a_korean_locale` | 한국어 locale에서 process_table()의 ps 실행이 PermissionError errno1로 거부됨. |
| `tests/test_human_e2e.py::test_the_profile_denies_a_connection_the_resolver_and_a_model_client` | nested sandbox-exec가 sandbox_apply 거부(exit71)로 시작하지 못해 stdout이 비고 IndexError 발생. |
| `tests/test_model_eval.py::test_the_dry_run_goes_through_aelix_and_is_scored` | 분석 dry-run은 success이나 ps 조회 거부로 after_exit.checked=false·processes_unchecked가 되어 하네스 exit1. |
| `tests/test_model_eval.py::test_a_key_the_model_echoes_is_in_no_file_of_the_run` | ps 조회 거부로 안전 확인이 processes_unchecked를 기록하여 기대 exit0 대신 exit1. |
| `tests/test_model_eval.py::test_the_child_gets_no_credential_variable_and_the_runs_own_home[0]` | ps 조회 거부로 안전 확인이 processes_unchecked를 기록하여 기대 exit0 대신 exit1. |
| `tests/test_model_eval.py::test_a_job_left_in_the_background_is_waited_for_before_the_scan` | ps 조회 거부로 after_exit.checked=false·processes_unchecked가 되고 background 작업을 기다리지 못해 late-copy.json 생성 단언 실패. |
| `tests/test_model_eval.py::test_a_timeout_stops_aelix_and_what_the_model_left_running` | ps 조회 거부로 종료 후 안전 확인이 processes_unchecked를 기록하여 기대 exit0 대신 exit1. |
| `tests/test_model_eval.py::test_a_login_the_api_refuses_is_not_run` | 가짜 로그인 API 거절과 별개로 ps 조회 거부에 따른 processes_unchecked가 추가되어 secret_scan의 passed 기대가 failed로 실패. |
| `tests/test_model_eval.py::test_the_launcher_masks_the_logins_exact_values_in_every_form_and_place` | ps 조회 거부로 토큰을 넣은 background command line이 run.json에 관찰되지 않아 exact.masked_files의 run.json 포함 단언 실패. |
| `tests/test_model_eval.py::test_a_harness_asked_to_stop_stops_aelix_and_still_searches_the_run` | ps 조회 거부로 sleeping()이 항상 빈 목록을 반환하여 bash 시작 관찰을 180초 기다린 뒤 실패. |
| `tests/test_model_eval.py::test_a_killed_harness_leaves_nothing_running_and_scan_secures_the_run` | ps 조회 거부로 sleeping()이 항상 빈 목록을 반환하여 bash 시작 관찰을 180초 기다린 뒤 실패. |

원시 로그·명령·시각·소스 불변 확인·환경 probe와 실패별 보조 근거는 run `7397fe0a-2fa9-465a-ae18-76caccbfee2e`의 `evidence/`에 남겼다.
세션 밖의 macOS CI가 sandbox·프로세스 추적 시험을 확인한다. CI의 정확한 HEAD·실행 번호·시도·결과는 PR 본문에 별도로 남기며,
로컬 실패와 CI 결과를 합쳐 하나의 통과 상태로 쓰지 않는다.

## 미실행과 다음 단계

동일 frozen commit의 구현 독립1·별도 Codex1·이후 수정 diff1은 **not_run**이다. 설계 리뷰는 PR #105 완료분을 유지한다.
소유자 지시로 이번 작성자 세션은 구현 리뷰를 실행하지 않고, exact-head 3OS CI 완료 뒤 Draft로 외부 리뷰 요청을 넘긴다.
브라우저·CSP·키보드·모바일·OS ZIP 도구·M 규모·다른 독자·소유자 수용, Report/collection 공유(PR4), publish/receipt(#21),
실제 모델/토큰은 별도 미실행이다. main에서 가져온 #132의 Q17 결과는 당시 코드의 증거이며 이번 PR의 새 사람 수용 결과로 올리지 않는다.
PR은 Draft이며 이 측정만으로 Ready·병합·릴리스 조건을 충족하지 않는다.
