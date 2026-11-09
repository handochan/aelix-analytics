# Issue #171 PR-2 — 관찰된 활동의 화면과 출처 추적 (2026-11-10)

[Issue #171](https://github.com/handochan/aelix-analytics/issues/171)의 둘째 구현 PR(ADR-0015 "구현 순서"의 PR-2)을 잰 기록이다. 기록의 형식은
PR-1([기록](2026-11-10-issue-171-observed-activity.md))이 정했고 이 PR은 읽기(Workspace와 C18)만 더한다. 새 기록 종류를 읽는 화면이라 ADR의
구현 순서대로 측정 기록을 남긴다. 수치의 정본은 [JSON](2026-11-10-issue-171-observed-screens.json)이다.

## 측정한 코드

- 커밋 `9f77671`(로컬 branch `feat/issue-171-observed-screens`, 리뷰 수정 뒤의 PR-1 `0bfba89` 위). 처음 잰 값은 리뷰 전 `bd057ce`였고, 구현 리뷰
  (독립 1회·Codex 1회, 2026-11-10)의 지적을 고친 뒤 다시 쟀다. PR-1과 같이 [PR #175](https://github.com/handochan/aelix-analytics/pull/175)·
  [PR #176](https://github.com/handochan/aelix-analytics/pull/176)을 로컬 병합한 위이며 두 PR이 main에 병합되면 rebase한다.
- 기기와 조건은 PR-1 기록과 같다(macOS 26.6.2 arm64, Apple M5 Pro, CPython 3.12.13, 다른 작업과 함께 쓴 기기). Linux·Windows는 push 뒤 CI.

## 비용

`screens171.py`(이 기기에만 둠, sha256 `b0361f6f…`): kit 함수로 관찰을 쓴 프로젝트의 `workspace.build`를 세 번(첫 번은 `workspace/`를 지운 뒤) 잼.

| 관찰 수 | 빌드 ms | 페이지 | HTML |
|---|---|---|---|
| 0 | 43 · 16 · 15 | 15 | 73 KB |
| 100 | 58 · 60 · 60 | 119 | 1.6 MB |
| 300 | 133 · 140 · 152 | 327 | 4.6 MB |

관찰도 다른 기록처럼 기록 페이지(`relations/`)가 하나씩 생긴다(관찰 하나에 약 15 KB). 한 파일의 같은 바이트를 50개 관찰이 보고 run 하나가 읽은
프로젝트에서 그 run의 출처 경로(`ReadModel.provenance`)는 0.8 ms였고, 후보 50개는 데이터 노드의 `observed_changes`에 모두 있고 경로는 예산대로
노드 20개를 보이고 나머지를 `omitted`로 셌다(확인 줄 `observed` 50).

## 확인할 것(ADR-0015 결정 7·8)과 시험

시험은 `tests/test_observed_screens.py`다.

| ADR의 항목 | 시험 |
|---|---|
| 후보가 더해져도 `producer_state`·확정 연결 수 불변, `changed_during`(`unverified`, 확정 아님), 관찰에서 멈춤, `observed` 확인 줄, 사용처 방향, 제한 조회의 같은 경로 | `test_bytes_a_python_command_wrote_have_the_observation_as_an_unverified_candidate` |
| 오전 run의 입력에 오후 관찰이 붙지 않음, 걸친 창의 `uncertain`, 지금 파일에서는 모든 관찰이 시간순 | `test_time_direction_before_across_and_after_a_run_s_start` |
| run과 겹친 바이트의 낮춤 | `test_bytes_a_run_also_recorded_are_marked_and_the_run_stays_the_producer` |
| `Missing now`의 삭제 관찰, 해시 없는 변경은 후보 아님 | `test_unknown_and_hashless_changes_are_no_candidates_and_a_deletion_is_said_of_a_missing_file` |
| 관찰을 인용한 기록의 `event` 참조 | `test_a_record_citing_an_observation_finds_it_and_stays_an_unconfirmed_source` |
| 첨부 `changes.json`의 후보(digest가 다르면 읽지 않음) | `test_an_observation_with_a_long_change_list_reads_its_attachment` |
| Timeline 행·배지·종류 목록 | `test_the_timeline_lists_an_observation_with_its_badge_and_its_own_kind_list` |
| 기록 페이지(가린 명령·전문 링크·사본·바뀐 파일·남기지 않은 것, canary 없음) | `test_the_record_page_shows_the_command_scripts_changes_and_what_is_not_kept` |
| Record coverage와 꺼진 세션의 그 뒤 bash 호출("at least") | `test_record_coverage_counts_observations_and_the_calls_of_a_session_after_its_observation_was_off` |
| run 페이지의 경로 그림과 문장 | `test_a_run_s_page_draws_the_observation_on_its_trail` |
| 해석하지 못하는 관찰 줄 | `test_a_row_whose_details_cannot_be_read_says_so` |
| 공유본의 데이터 노드에 관찰 후보 없음 | `test_a_share_shows_no_observation_candidate`, PR-1의 `test_a_share_holds_no_observation_and_its_content_hash_does_not_move` |
| 구현 리뷰의 지적: 관찰만 본 경로의 출발점, 관찰에서 사용처로 갈 때의 시간 방향, 끈 호출을 뺀 그 뒤 호출 수와 건너뛴 호출 수, 확인하지 못한 변경의 문구, `files.json` 링크, 기존 페이지의 표식 불변 | `test_a_path_only_an_observation_saw_is_a_start`, `test_what_uses_an_observation_s_bytes_follows_time`, `test_record_coverage_leaves_out_the_turning_off_call_and_counts_skipped_calls`, `test_an_observation_of_unknown_changes_only_says_it_could_not_check`, `test_a_record_written_with_its_lists_attached_links_them`, `test_pages_of_a_project_without_observations_carry_no_new_words` |

## 실행한 검증

| 검사 | 결과 |
|---|---|
| 개발 환경 전체 시험 | `9f77671`(clean): 3359 passed, 148 skipped(708초). 처음 `bd057ce`: 3318 passed, 148 skipped |
| aelix 0.1.0b2 통합 다섯 파일(3.12) | `9f77671`: 1057 passed. 처음 `bd057ce`: 1053 passed |
| `ruff check` · `tools/check_docs.py` · `tools/release_check.py version` | 통과 · 오류 0 · 0.32.1 |

## 하지 않은 것

- Linux·Windows, 브라우저로 본 화면(Chromium E2E·좁은 화면·forced-colors)과 사람의 확인, Workspace S/M fixture 규모 측정(`tools/workspace_scale_eval.py`)에 관찰을 넣은 것.
- 리뷰 수정의 수정 diff 확인(대기).
