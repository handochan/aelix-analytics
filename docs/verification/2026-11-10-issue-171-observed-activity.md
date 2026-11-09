# Issue #171 PR-1 — 관찰된 활동의 쓰기와 최소 읽기 (2026-11-10)

[Issue #171](https://github.com/handochan/aelix-analytics/issues/171)의 첫 구현 PR(ADR-0015 "구현 순서"의 PR-1)을 잰 기록이다. 새 기록 종류
(`observed_activity`)를 더하는 기록 형식 PR이므로 [검증 증거의 기준](../PROJECT-OPERATIONS.md#검증-증거의-기준)에 따라 남긴다. 수치·명령·해시의 정본은
[JSON](2026-11-10-issue-171-observed-activity.json)이다. 측정에 쓴 임시 프로젝트와 원시 출력은 이 기기의 `.devstate/`에만 두고 커밋하지 않았다.

## 측정한 코드

- 커밋 `d87a8f1`(로컬 branch `feat/issue-171-observed-activity`): main `6fd956c`에 병합 전인 [PR #175](https://github.com/handochan/aelix-analytics/pull/175)
  (ADR-0015, `47f2c50`)와 [PR #176](https://github.com/handochan/aelix-analytics/pull/176)(#170의 판정, `7df7d31`)을 로컬 `--no-ff`로 병합한 위의 구현이다.
  두 PR이 main에 병합되면 rebase하므로 커밋 해시가 바뀐다. rebase 뒤 문서가 아닌 차이가 생기면 병합 전에 다시 잰다.
- 기기: macOS 26.6.2 arm64(Apple M5 Pro, 18코어, 64 GB), 로컬 APFS SSD의 임시 디렉터리, CPython 3.12.13, aelix 0.1.0b2. 다른 작업이 같은
  기기를 쓰는 동안 쟀으므로 시간은 대략의 값이다. Linux·Windows는 이 PR을 push한 뒤 CI가 같은 시험(`record_observation`의 `observed-cost-*`)으로
  남긴다(지금 `not_run`).

## 비용 상한(OQ4)

**확장의 두 hook을 거친 한 bash 호출**(`measure171.py`: 이 파일을 aelix가 파일을 로드하는 방식으로 로드하고 aelix 0.1.0b2의 `ToolCallHookEvent`·
`ToolResultHookEvent`로 `on_tool_call`·`on_tool_result`를 부름. 호출마다 `data/derived/`에 파일 하나를 씀. 한 세션의 연속 4번, 첫 번은 그 세션의
첫 상태 보기):

| `data/` | 앞(`tool_call`) ms | 뒤(`tool_result`) ms | 결과 |
|---|---|---|---|
| 파일 1,000개(디렉터리당 100) | 37.2 · 37.4 · 28.3 · 29.8 | 37.6 · 30.6 · 31.9 · 29.6 | 매번 `observed`, 줄 2,062 B |
| 파일 10,000개 | 59.8 · 62.3 · 57.3 · 55.9 | 59.4 · 67.0 · 61.1 · 59.7 | 매번 `observed`, 줄 2,062–2,063 B |
| 파일 50,000개 | 74.9 · 9.2 · 9.2 · 9.1 | 10.2 · 10.3 · 9.8 · 9.7 | 첫 번 `not_observed`(`too_many_entries`)와 세션 끄기, 그 뒤 `observation_off` |
| 한 디렉터리에 항목 25,000개 | 22.3 · 9.6 · 0.6(`ls`) | 10.2 · 9.8 · 0.3 | 첫 번 `too_many_entries`, 그 뒤 Python만 기록·`ls`는 기록 없음 |

- 한 bash 호출에 더해진 지연은 파일 1만 개에서 약 120 ms였다(ADR의 측정 "두 번의 상태 보기 약 100 ms"와 같은 범위). 상한(앞 2초, 뒤 3초)에 닿은
  호출은 없었다. 상한을 넘는 `data/`는 정렬 전에 20,000번째 항목에서 멈춰 75 ms 안에 끝났고, 그 세션의 다음 호출은 상태를 보지 않아 10 ms 안팎이다.
- kit 함수만(`tests/test_observed.py`의 측정 시험, 확장·asyncio 없이): 1,000개 앞 3.4 ms·뒤 3.8 ms, 10,000개 앞 58.9 ms·뒤 76.8 ms,
  한 디렉터리 25,000개는 12.8 ms에 멈춤.
- **해시**(뒤 단계에서 바뀐 파일 하나): 64 MiB 34 ms, 200 MiB 88 ms(둘 다 `hashed`), 512 MiB는 합 상한 256 MiB를 넘어 읽지 않고 `over_budget`(11 ms)이며
  그 변경은 `unknown`이라 알림이 "could not check"라고 말했다.
- **줄 크기**: 보통의 관찰 2,073 B, 바뀐 파일 400개 13,705 B(인라인 목록은 12 KiB까지, 전체는 `changes.json`), 20 KB 명령 6,167 B(인라인 4 KiB, 전문은
  `command.txt`). 직렬화한 `details`가 24 KiB를 넘을 때의 최소 details는 상한을 낮춘 시험이 쓴다.
- **상한을 지키는 쪽**(시험의 단언): 항목마다 느려진 walk가 0.5초 기한에서 1초 안에 멈춤, 멈춘 앞 작업은 1.8–3.0초에 버리고 다음 호출은 `busy`, 3초를
  넘긴 뒤 작업은 2.8–4.0초에 결과를 돌려주며 커밋 전이면 "not recorded", 커밋 뒤면 "could not confirm", 다른 쪽이 잡은 journal 잠금은 0.6초 기한에서
  1.5초 안에 `journal_busy`.

## 가림(결정 5)과 공유본(결정 10)

- 규칙마다 canary(25가지: 환경 변수·`export`·`env`·PowerShell `$env:`·코드 대입·사전 키·`--name=`·`--name value`·`-u user:password`·URL 비밀번호·
  모든 질의 값·`Authorization`·`Cookie`·`X-Api-Key`·`Basic`·PEM·JWT·AWS·GitHub 두 꼴·GitLab·`sk-ant-`·`sk_live_`·Slack·Google·`hf_`·`npm_`)를 짧은 명령과
  인라인 상한을 넘는 긴 명령(`command.txt` 첨부) 두 번씩 기록했다. 값과 그 sha256, 가리기 전 명령의 sha256이 journal과 첨부 어디에도 없었다.
  telemetry의 `summary`도 같다(확장 시험). 키 꼴은 시험이 실행 중에 조립한다(저장소 키 검사).
- 비밀일 수 있는 이름 10가지(`.env`·`.env.local`·`*.pem`·`*.key`·`.netrc`·`.pgpass`·`id_rsa`·`id_ed25519.pub`·`credentials.json`·이름에 `secret`)의
  변경은 `hash: withheld`이고 그 바이트의 sha256이 journal에 없었다. 가림 규칙이 걸리는 스크립트는 사본도 해시도 없었다(`secret_like`).
- read·evidence 공유본은 관찰을 담지 않았고(명령 marker와 `observed_activity`가 패키지의 어느 파일에도 없음), 관찰만 더한 뒤 다시 build한
  `content_sha256`·`content_seq`가 같았다. `details`를 해석하지 못하는 관찰 줄은 내용 기준을 움직였다(ADR-0014 결정 2의 규칙 그대로).

## 옛 kit과 floor(결정 12)

`older171.py`: 이 커밋의 kit으로 관찰 하나를 쓴 프로젝트(e3, 표식 floor 0.32.1)를 두 체크아웃으로 읽고 썼다(`PYTHONDONTWRITEBYTECODE=1`, 체크아웃은 그대로).

| kit | 읽기(`status`) | 쓰기(`record note`) |
|---|---|---|
| 0.32.0 후보 `26c28d0` | `unsupported: [journal:3 unknown_kind]`, 기록 2개 | `writer_too_old`로 거절 |
| 구현 전 0.32.1 체크아웃(main `6fd956c`) | `unsupported: [journal:3 unknown_kind]` | 씀(e4, 관찰 뒤의 seq). ADR-0015 결정 12의 한계 그대로 |
| 이 커밋 | `unsupported` 없음, 관찰 1·최근 목록에서 뺌, 줄 `observed outside the kit: 1 (latest e3; …)` | — |

## 확인할 것(ADR-0015)과 시험

시험은 `tests/test_observed.py`(kit, 개발 환경)와 `tests/test_extension.py`의 #171 절(aelix 0.1.0b2의 로더·harness·bash 도구 포함)이다.

| ADR의 항목 | 시험 |
|---|---|
| 기록 조건: kit 밖 Python, 설명되지 않은 변경, kit CLI 하나, `analytics status; python analysis.py`, `python -c 'import aelix_analytics; …'` 쓰기, `uv run analytics run x.py 2>&1 \| tail -20`, `pip install`·`python --version`, `ls`, 다른 확장이 막은 호출, 읽기 전용·옛 형식, 밖 작업 디렉터리의 Python | `test_the_real_host_records_an_observed_activity_and_tells_the_model`, `test_only_a_line_that_is_one_kit_command_line_skips_the_state`, `test_an_analysis_step_is_python_outside_the_kit_that_does_not_only_manage_packages`, `test_the_sdk_through_python_c_is_looked_at_and_its_write_recorded_as_a_change`, `test_a_call_another_extension_blocks_is_not_observed_and_its_start_is_dropped`, `test_read_only_and_older_projects_are_neither_looked_at_nor_written`, `test_a_python_command_that_names_nothing_in_the_project_from_outside_it_is_not_recorded`, `test_a_call_with_nothing_to_record_writes_nothing`, `test_a_command_that_is_no_python_is_recorded_for_the_changes_no_run_recorded` |
| 상태: 만들기·고치기·지우기, mtime 보존 교체, 같은 바이트, 캐시 A→B→A, 링크 경계, 공유 위반, 해시 중 변경, 창 안 run·가져오기, 다른 프로세스의 쓰기, journal 꼬리 실패 | `test_created_modified_and_deleted`, `test_a_replacement_that_keeps_size_and_mtime_is_hashed_from_its_bytes`, `test_the_same_bytes_put_back_are_no_change_when_the_start_read_them`, `test_cache_a_then_b_at_the_start_then_a_again_is_not_dropped`, `test_a_link_made_to_a_full_directory_is_not_thousands_of_files_created`, `test_a_file_whose_stat_fails_after_is_unreadable_not_deleted`, `test_a_file_that_changes_while_it_is_hashed_is_unstable`, `test_a_run_and_an_import_in_the_window_explain_their_files`, `test_explained_and_unexplained_changes_side_by_side`, `test_a_journal_end_that_cannot_be_read_leaves_the_changes_unknown` |
| 스크립트: 시작 때 있던 것, heredoc, 창 안 변경, 바뀐 명령, `cd sub`·`cd "$X"`, git 무시·`data/`의 `.sql`·비밀 이름·비밀 내용·`.ipynb`, 밖의 스크립트, 같은 바이트, 상한 | `test_scripts_before_after_and_changed_in_the_window`, `test_a_command_another_extension_changed_keeps_the_start_bytes_of_its_own_names_only`, `test_cd_is_followed_when_literal_and_not_after_a_variable`, `test_which_scripts_are_copied`, `test_a_git_work_tree_is_asked_which_scripts_it_ignores`, `test_the_script_bounds` |
| 대기·취소: 큰 단일 디렉터리, 연속 시간 초과와 cold 첫 걷기, 세션 끄기와 `observation_off`, 멈춘 읽기와 `busy`, 커밋 뒤 상한, 처리 중 취소, 처리기 예외, `unknown`만의 문구, 오지 않은 `tool_result`, 잠금 실패, 최소 details | `test_a_big_single_directory_stops_the_view_before_it_is_sorted`, `test_a_view_past_its_deadline_is_not_observed_and_three_in_a_row_turn_the_session_off`, `test_the_before_view_keeps_its_bound_on_a_slow_walk`, `test_turning_the_session_s_observation_off_is_said_once`, `test_a_stuck_start_is_abandoned_within_its_bound_and_the_next_one_is_busy`, `test_an_end_past_its_bound_says_whether_it_could_have_been_recorded`, `test_work_cancelled_before_its_commit_writes_no_event_and_removes_its_folder`, `test_a_cancel_while_the_end_is_waited_for_goes_up_and_leaves_the_result_as_it_was`, `test_a_failing_observation_does_not_break_the_turn`, `test_only_unknown_changes_get_their_own_words`, `test_a_start_no_result_follows_is_dropped`, `test_a_journal_lock_held_elsewhere_records_nothing_within_the_bound`, `test_details_over_their_budget_are_written_whole_in_attachments`, `test_a_long_change_list_goes_to_its_attachment` |
| 가림 | `test_each_rule_redacts_its_canary_from_text_details_and_attachments`, `test_redaction_keeps_what_it_need_not_hide`, `test_a_secret_name_is_listed_but_never_hashed`, `test_which_scripts_are_copied`, `test_the_telemetry_summary_is_redacted` |
| 호환 | `test_the_floor_refuses_an_older_writer_and_an_older_reader_counts_the_line`, `test_only_the_observation_writes_the_kind`, `test_the_schema_and_the_reader_refuse_what_is_no_observation`, 위 `older171.py` |
| 읽기(PR-1 범위)와 공유본 | `test_status_counts_observations_on_a_line_of_their_own`, `test_a_row_whose_details_cannot_be_read_is_a_record_but_no_observation`, `test_the_publication_content_basis_does_not_move_for_an_observation`, `test_a_share_holds_no_observation_and_its_content_hash_does_not_move`, `test_the_workspace_still_builds_with_an_observation_in_the_journal` |
| run과 함께 쓰는 상태 비교 | `test_a_run_keeps_its_outputs_through_the_shared_state_module`, 기존 `tests/test_path_identity.py`·`tests/test_platform.py` |
| 측정 | `test_the_cost_of_an_observation_is_measured`, `test_the_cost_past_the_entry_bound_is_measured`, 위 `measure171.py` |

출처 추적의 후보·시간 방향·`uncertain`·run과 겹친 바이트·`Missing now`와 Workspace의 화면은 PR-2의 항목이라 이 기록의 범위 밖이다.

## 실행한 검증

| 검사 | 결과 |
|---|---|
| `uv run --locked pytest -q --timeout 600 --timeout-method=thread -p no:cacheprovider`(개발 환경 전체) | 3305 passed, 148 skipped(925초, `d87a8f1`과 같은 작업 트리) |
| aelix 0.1.0b2 통합 다섯 파일(`test_extension`·`test_asset_extension`·`test_checkpoint_extension`·`test_usage_report`·`test_agent_cli`) | 3.12: 1053 passed, 3.11: 1053 passed |
| `python3.11 tools/platform_smoke.py --stdlib-only --gate required` | holds 15, violated 0 |
| `uv run --locked ruff check tests extension src tools` · `tools/check_docs.py` · `tools/release_check.py version` | 통과 · 오류 0 · 0.32.1 |
| `d87a8f1`에서 `tests/test_observed.py tests/test_line_endings.py` | 166 passed |

## 하지 않은 것

- Linux·Windows의 비용과 시험(push 뒤 CI), cold cache(OS 파일 캐시 비우기)·네트워크 드라이브·FAT, 실제 모델 세션, 시스템 호출에서 멈춘 읽기의 실제
  네트워크 마운트(멈춘 작업은 함수 대역으로 재현함).
- 독립 리뷰·Codex 리뷰(구현 리뷰 대기), PR-2(Workspace·C18).
