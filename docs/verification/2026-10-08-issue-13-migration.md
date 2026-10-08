# Issue #13 복사 전환 엔진 측정(PR ②) — 2026-10-08

[#13](https://github.com/handochan/aelix-analytics/issues/13)의 둘째 구현 PR ②(kit 0.7.0, 번호 그대로)는 `lab.json` 프로젝트를 새 폴더에 새 형식으로
복사하는 전환 엔진(`aelix_analytics.migration`), 포트 메서드 넷(`FileLocks.try_exclusive`, `AtomicFiles.publish_new`·`flush_handle`·`flush_dir`),
전환 기록 `analytics.migration/1`과 표식의 `migration`, 커밋 전 목적지의 판정 `incomplete`(`migration_incomplete`), 전환한 프로젝트의 매핑 읽기를
더합니다. 기존 프로젝트를 변환하는 일이므로 운영 정책의 측정 기록이며, [설계](../design/RECORD-WRITER-AND-MIGRATION.md) 12절이 이 기록에 더한 키
`fixtures`·`phases`·`sizes`·`preservation`·`fault_matrix`·`concurrency`·`reader_comparison`이 있습니다. 개발 기기(macOS)의 값과 함께,
Linux·Windows·호스트 macOS의 값은 [PR #82](https://github.com/handochan/aelix-analytics/pull/82)의 CI가 이 코드를 담은 헤드 `ec8d7d9`에서
돌린 것입니다(`pull_request` 실행과 `ci:macos` 라벨 실행, 각 시도 1, 모든 job 통과). 앞 헤드 `53d1bdf`의 실행은 Windows 레그가 실패했고, 그
원인과 수정은 아래 "Windows CI 실패와 수정"에 있습니다. 이 칸을 채운 커밋은 이 기록만 바꿉니다. 모든 값과 JUnit 파일의 sha256은 [2026-10-08-issue-13-migration.json](2026-10-08-issue-13-migration.json)에
있습니다. 계약에 반영한 것은 [CONTRACTS.md](../CONTRACTS.md) B("표식 판정과 쓰기", "프로젝트 찾기", "프로젝트 전환")·D·E·F·F1(kit 0.7.0)·G입니다.

전환은 아직 명령으로 열지 않았습니다(`analytics migrate`는 셋째 PR). 그래서 아래의 전환은 모두 시험과 이 측정의 스크립트가 엔진을 직접 부른
것입니다.

이 기록은 PR의 리뷰(독립 구현 리뷰 1회, Codex 리뷰 1회)의 지적을 고친 커밋, 그 수정 diff 확인의 지적을 고친 커밋, 그리고 PR #82의 Windows CI가
실패한 사례 넷의 원인을 고친 커밋(`fix: the migration engine's Windows cases (Issue #13 PR 2)`, `ebd10b6`)의 코드를 처음부터 다시 잰 것입니다. 그
커밋이 `migration.py`·`core.py`·`_platform.py`를 고쳤으므로 모든 값을 다시 쟀고, 앞선 세 측정(처음 코드 `2b6eabb`, 리뷰 뒤 코드 `80a0fc9`, 수정
diff 확인 뒤 코드 `84f3970`)과의 비교는 끝의 "리뷰 뒤 다시 잼"·"수정 diff 확인 뒤 다시 잼"·"Windows 수정 뒤 다시 잼"과 JSON의
`previous_measurement`·`review_fix_measurement`·`fix_check_measurement`에 있습니다.

## 출처와 환경

| 항목 | 근거 |
|---|---|
| 측정한 코드 | `feat/issue-13-migration-engine`(PR #82)의 커밋 `fix: the migration engine's Windows cases (Issue #13 PR 2)`(`ebd10b6`)의 코드. `origin/main` `ed29cf0`(PR #80 병합, 트리는 `fbd12e1`과 같음) 위의 `b7e1521`·`8ca6914`·`50a55bb`·`d08cf24`·`4243021`·`f7faea5`·`80a0fc9`·`84f3970`·`53d1bdf`와 그 커밋입니다. 그 커밋을 run 폴더의 깨끗한 클론에서 쟀고, 이 기록은 그 뒤의 docs 커밋입니다(코드를 바꾸지 않음). JSON의 `measured_tree`는 파일 manifest의 형식(추적하는 `src`·`extension`·`tests`·`tools`·`bin`·`pyproject.toml`·`uv.lock` 파일마다 `shasum -a 256` 한 줄, 경로 순), 그 manifest 전체의 sha256과 파일 수를 담습니다. 파일마다의 목록은 run 폴더의 `evidence/code-manifest.txt`에 있고 저장소에는 없습니다 |
| 비교한 기준 | `fbd12e1`(= `c918381`의 코드, 이 PR이 올라가는 PR #80의 헤드)의 체크아웃에서 처음 측정의 새 시험을 돌렸고, 리뷰 뒤 더한 시험은 그때의 앞 헤드 `f7faea5`, 수정 diff 확인 뒤 더한 사례는 `80a0fc9`, Windows 수정의 시험은 `53d1bdf`의 코드에서 돌렸습니다(아래) |
| 워크플로 | `.github/workflows/ci.yml`, 이 변경이 고치지 않음 |
| 코드 실행(Linux, Windows) | [37789500898](https://github.com/handochan/aelix-analytics/actions/runs/37789500898), 시도 1, `pull_request`(`synchronize`)로 헤드 `ec8d7d9`(측정한 `ebd10b6` 뒤에 바뀐 것은 이 기록뿐; 워크플로는 GitHub의 병합 커밋 `0edaff1`, 기준 `ed29cf0`). `platform · ubuntu-24.04` job 113352793123, 15m20s(ImageOS `ubuntu24 20260927.320.1`, floor job은 `20261004.327.1`; Linux-6.17.0-1022-azure-x86_64-with-glibc2.39, CPython 3.12.3·3.11.15, floor 3.11.17). `platform · windows-2025` job 113352791801, 36m53s(ImageOS `win25-vs2026 20260925.250.1`, Windows-2025Server-10.0.26100-SP0, CPython 3.12.10·3.11.9, floor 3.11.9). lint job 통과(ruff, 문서 검사: Markdown 65, 로컬 링크 692, 오류 0). 앞 헤드 `53d1bdf`의 실행([37757801858](https://github.com/handochan/aelix-analytics/actions/runs/37757801858)·[37757804883](https://github.com/handochan/aelix-analytics/actions/runs/37757804883))은 Windows 레그만 실패(아래 "Windows CI 실패와 수정", JSON의 `windows_ci_failure`) |
| macOS 실행(라벨) | [37789525327](https://github.com/handochan/aelix-analytics/actions/runs/37789525327), 시도 1, 같은 헤드의 `ci:macos` 라벨(`labeled`): `platform · macos-26` job 113352876499, 15m16s(ImageOS `macos26 20260907.0351.1`, macOS-26.6.2-arm64-arm-64bit, CPython 3.12.10·3.11.9, floor 3.11.9) |
| 그 뒤의 실행 | `8c13679`(이 기록의 CI 칸)의 [37795999482](https://github.com/handochan/aelix-analytics/actions/runs/37795999482)는 Windows 레그가 두 번 실패했습니다. 시도 1은 이 PR과 무관한 `tests/test_model_eval.py`의 `settle` 시간 초과([#83](https://github.com/handochan/aelix-analytics/issues/83))이고, 시도 2는 이 PR의 잠금 시험 둘의 경합입니다: 같은 프로세스의 스레드가 잠금을 놓기 전에 다시 잡으려 함(`tests/test_contracts.py`), kill한 run의 잠금을 Windows가 늦게 풂(`tests/test_migration.py`, CONTRACTS G). 다음 commit은 그 두 시험과 CONTRACTS G만 고쳤고 엔진 코드는 측정한 것과 같습니다 |
| 개발 기기 | macOS 26.6.2 arm64(Apple M5 Pro), uv 0.11.19. 저장소 시험 CPython 3.12.13(`uv.lock`의 개발 환경), aelix 레그 3.12·3.11, 런처·stdlib 스모크 3.11. 같은 기기에서 다른 에이전트의 시험이 함께 돌았으므로 시간은 그 부하를 담습니다 |
| 격리 | `AELIX_ANALYTICS_TRUST_FILE`·`AELIX_ANALYTICS_UV`를 run 폴더 안으로(`uv`는 실제 uv로의 링크), `LAB_*`·`AELIX_LAB_*` 없음, `~/.aelix`는 읽지도 쓰지도 않음 |

## 결과

| 검사 | 명령 | 결과 |
|---|---|---|
| ruff | `uv run --locked ruff check tests extension src tools` | 통과 |
| 저장소 시험 | `uv run --locked pytest -q -ra -p no:cacheprovider --timeout 600 --timeout-method=thread` | 통과: 1647 passed, 92 skipped, 0 failed(1095초). 수정 diff 확인 뒤 측정과 같은 수(Windows 수정은 기존 사례에 단언을 더함). 리뷰 뒤 측정(1644 passed)보다 수정 diff 확인에 더한 사례 3개가, 처음 측정(1621 passed)보다 리뷰에 더한 시험 23개가 많고, PR ①의 마지막 측정(1477 passed, 72 skipped)보다 170개 많음: 전환 시험 `tests/test_migration.py`·`tests/test_migration_reader.py`, 포트 계약 사례, 표식 판정의 `incomplete` 행, Windows의 `publish_new`, aelix 프로세스가 `migration`을 로드하지 않음. 늘어난 건너뜀 20개는 Windows 구현의 포트 사례 14, 시험 adapter가 밝히지 않은 능력 3, 다른 OS에서 도는 전환 사례 3(공유 없이 연 원본 파일과 MAX_PATH는 Windows, UTF-8이 아닌 이름은 Linux)입니다. 리뷰에 더한 시험 가운데 읽기 전용 폴더와 링크 뒤 `..`의 둘은 POSIX에서만 돌고 이 기기에서 돌았습니다 |
| 계층 검사 | `tests/test_layers.py` | 통과: 47 passed(`migration` 계층 등록, ARCHITECTURE §11의 표와 같음) |
| 계약 시험 | `tests/test_contracts.py` | 통과: 66 passed, 41 skipped(Windows 구현은 Windows 레그, 시험 adapter가 밝히지 않은 능력) |
| aelix 0.1.0b2, 3.12 / 3.11 | `tests/test_extension.py tests/test_usage_report.py`(CONTRIBUTING의 격리 명령) | 통과: 161 / 161 passed |
| 런처, 3.11 | `tests/test_launcher.py` | 통과: 138 passed, 1 skipped(Windows의 동작) |
| 사용량 보고서 | `tools/usage_report.py`(aelix 0.1.0b2, 3.12) | 필수 사실 58/58, 불변식 42/42, exit 0 |
| 플랫폼 스모크 | `tools/platform_smoke.py --gate required` | hold: 25 holds, 0 violated |
| stdlib 스모크, 3.11 | `tools/platform_smoke.py --stdlib-only --gate required` | hold: 15 holds, 0 violated, `not_run` 2(uv를 쓰지 않는 방식) |
| 문서 검사 | `tools/check_docs.py --output <run 폴더>/evidence/docs-record.json`(이 기록을 더한 worktree에서) | `ok: true`, 오류 0(Markdown 65, 로컬 링크 692) |
| 공백 | `git diff --check fbd12e1...HEAD`(잰 클론), `git diff --check origin/main...HEAD`(이 기록을 더한 커밋) | 둘 다 출력 없음 |
| 새 시험을 기준 소스에서(처음 측정) | 그때 헤드의 새·바뀐 시험 + `fbd12e1`의 `src`·`extension`·`bin`·`tools`·`pyproject.toml`·`uv.lock` | 기대대로 실패: 70개(수집 오류 둘 — `tests/test_migration.py`·`tests/test_migration_reader.py`는 `aelix_analytics.migration`이 없음 — 포함). 통과한 24개는 시험 adapter(시험 코드인 `tests/platform_doubles.py`)의 포트 사례와, 거른 조건에 함께 든 바꾸지 않은 사례(쓸 수 없는 pid, 계층 검사의 파서 사례 넷)입니다 |
| 리뷰의 새 시험을 앞 헤드에서 | 리뷰 수정 커밋(`80a0fc9`)의 `tests/test_migration.py`·`tests/test_migration_reader.py` + 그때의 앞 헤드 `f7faea5`의 코드(run 폴더의 클론) | 기대대로 실패: 리뷰에 더한 23개 모두(지적마다의 재현). 그 헤드에 있던 110개는 통과, 2개 건너뜀 |
| 수정 diff 확인의 새 사례를 앞 헤드에서 | 수정 diff 확인 커밋(`84f3970`)의 `tests/test_migration.py`에서 바꾸거나 더한 사례 + `80a0fc9`의 코드(run 폴더의 클론) | 기대대로 실패: 더한 3개 모두(다른 전환의 보고서 바이트·매핑 바이트를 자기 디렉터리에 둔 표식, 원본 재확인 뒤 지운 계획 폴더). 리뷰가 더한 `report_path` 사례는 통과 |
| Windows 수정의 시험을 앞 헤드에서 | `ebd10b6`의 `tests/test_migration_reader.py` + `53d1bdf`의 코드(run 폴더의 클론) | 기대대로 `test_the_mapping_s_rules_are_by_path_components` 하나만 실패(`..`가 든 절대 경로를 `'sub/../x/y.csv'`로 읽음, 기대는 `'x/y.csv'`), 나머지 4개 통과(JSON의 `windows_ci_failure.local_check`). Windows에서만 보이는 원인 셋은 이 기기에서 돌릴 수 없음 |

CI의 레그(헤드 `ec8d7d9`, 위 두 실행):

| 검사 | Linux | Windows | macOS(호스트) |
|---|---|---|---|
| 저장소 시험(`AELIX_ANALYTICS_TEST_INSTALL=1`, 배포본 end-to-end 포함, `tests/test_windows_console.py`는 따로) | 1,658 passed, 60 skipped, 0 failed | 1,525 passed, 216 skipped, 0 failed | 1,657 passed, 61 skipped, 0 failed |
| 그 가운데 `tests/test_migration.py` | 131 passed, 2 skipped | 127 passed, 7 skipped | 131 passed, 2 skipped |
| 그 가운데 `tests/test_migration_reader.py` | 5 passed | 6 passed(junction판 포함) | 5 passed |
| 그 가운데 `tests/test_contracts.py`의 새 포트 메서드 사례(`try_exclusive`·`publish_new`·`flush_handle`·`flush_dir`) | 25 passed, 17 skipped(POSIX 구현 14 passed, 시험 adapter 11 passed·3 skipped, Windows 구현 14 skipped) | 23 passed, 19 skipped(Windows 구현 12 passed·2 skipped, 시험 adapter 11 passed·3 skipped, POSIX 구현 14 skipped) | 25 passed, 17 skipped(Linux와 같음) |
| F 표(F1–F16, 37행) | 37 passed | 35 passed, 2 skipped(F6·F12의 link와 unlink 사이) | 37 passed |
| aelix 0.1.0b2 통합, CPython 3.12 / 3.11 | 161 / 161 passed | 162 / 162 passed | 161 / 161 passed |
| 런처, CPython 3.11 | 138 passed, 1 skipped | 64 passed, 75 skipped(POSIX의 사례) | 138 passed, 1 skipped |
| 플랫폼 스모크, required gate | hold: 25 holds, 0 violated | hold: 21 holds, 3 declared limitations, 0 violated | hold: 25 holds, 0 violated |
| stdlib 스모크, CPython 3.11 | hold: 15 holds, 0 violated(3.11.17) | hold: 14 holds, 1 declared limitation, 0 violated(3.11.9) | hold: 15 holds, 0 violated(3.11.9) |
| 사용량 보고서 | 필수 사실 58/58, 불변식 42/42 | 58/58, 42/42 | 58/58, 42/42 |
| 네이티브 콘솔, LongPathsEnabled=0의 긴 경로 | — | 21 passed / 7 passed(전환 사례 포함) | — |
| F 표의 관찰(`issue-13-migration/fault-matrix.json`) | 개발 기기와 같음(35행) | 개발 기기와 같음(33행) | 개발 기기와 같음(35행) |

- 새 시험 파일: `tests/test_migration.py`의 건너뜀은 Linux가 공유 없이 연 원본 파일(Windows)과 원본의 다른 철자(대소문자를 구별하는 파일
  시스템), 호스트 macOS가 공유 없이 연 원본 파일과 UTF-8이 아닌 이름(Linux)입니다. Windows의 일곱은 F6·F12의 link와 unlink 사이(Windows는
  rename으로 게시해 그 틈이 없음), 폴더 쓰기 권한의 사례 둘·링크 뒤 `..`·Windows가 만들지 않는 예약 이름(POSIX의 것), UTF-8이 아닌 이름(Linux)이고,
  Windows에는 겹치는 목적지 거절의 junction판이 하나 더 있습니다. `tests/test_migration_reader.py`는 세 레그 모두 통과했습니다(Windows는
  junction판 포함). 포트 계약의 Windows 구현에서 건너뛴 둘은 그 구현이 밝히지 않은 능력(폴더 권한, 링크로 게시)입니다.
- 이 PR이 기존 파일에 더한 사례: `tests/test_platform.py`의 Windows `publish_new`(바꿔 끼운 호출) 4개와 `tests/test_marker_gate.py`의 표식 없는
  전환 목적지 3개는 세 레그 모두 통과, `tests/test_windows_long_paths.py`의 MAX_PATH 넘는 목적지로의 전환은 Windows의 저장소 시험과
  LongPathsEnabled=0 실행에서 모두 통과(다른 레그는 건너뜀).
- Windows CI 실패의 네 사례(`test_the_source_s_own_refusals`, `test_a_source_file_another_program_holds_open_is_reported`,
  `test_the_reader_reads_a_migrated_project_as_it_read_its_source[symlink]`·`[junction]`)와 Windows 수정이 단언을 더한
  `test_the_mapping_s_rules_are_by_path_components`(두 구분자가 섞인 경로)는 이 헤드의 Windows 레그에서 모두 통과했습니다(JSON의
  `windows_ci_failure.at_the_fixed_head`).
- 개발 기기가 인용한 시험(JSON의 `developer_machine.suite.cited_cases` 198개를 레그마다 찾은 `legs.<os>.junit.suite.cited_cases`): 호스트
  macOS는 개발 기기와 같고(175 passed, 23 skipped), Linux는 둘이 다릅니다(UTF-8이 아닌 이름의 거절이 돌아 통과, 원본의 다른 철자는 건너뜀).
  Windows는 168 passed, 30 skipped로, 포트 사례의 POSIX 구현과 POSIX의 전환 사례가 건너뛰고 Windows 구현과 공유 없이 연 원본 파일의 사례가
  통과했습니다.
- F 표의 관찰: `tests/test_migration.py`가 레그마다 남긴 `issue-13-migration/fault-matrix.json`(행마다 끊긴 뒤 남은 경로, 다시 부른 결과, ledger의
  마지막 네 단계)이 세 레그 모두 개발 기기의 같은 관찰(run 폴더의 `evidence/observations/`)과 같습니다(전환 id를 가리고 구분자를 `/`로 맞춰
  비교; JSON의 `legs.<os>.observations.fault_matrix`). Windows는 건너뛴 두 행이 없고, F15의 두 행은 다른 시험이라 그 파일에 없습니다.
- Windows의 aelix 레그가 하나 많은 것은 junction판 사례
  (`test_status_in_the_aelix_process_names_a_linked_data_directory_s_datasets_by_project_paths[junction]`)입니다. Windows의 플랫폼
  스모크가 적은 제한 셋과 stdlib 스모크의 하나는 [#36의 기록](2026-10-06-issue-36-path-identity.md)에도 있던 같은
  선언(`kill_tree_after_the_leader_exited`, regex·자식 대기의 취소)이고 이 변경과 관계없습니다.
- CI의 저장소 시험은 배포본 end-to-end를 포함하고 콘솔 시험을 따로 돌리므로, 위 개발 기기의 수(콘솔 21개와
  `AELIX_ANALYTICS_TEST_INSTALL=1`이 필요한 11개가 건너뜀)와 같은 묶음이 아닙니다. Linux 레그의 floor job은 platform job과 다른 러너
  이미지(`ubuntu24 20261004.327.1`)였습니다. CI 레그별 값과 job id·시각, JUnit·스모크·사용량 보고서·관찰 파일의 sha256은 JSON의
  `runs`·`legs`에 있습니다.

## 중단과 이어 쓰기(`fault_matrix`, T6)

[설계](../design/RECORD-WRITER-AND-MIGRATION.md) 6.11절의 F1–F16을 `tests/test_migration.py`의 `FAULTS`가 한 행씩 돌립니다. 풍부한 원본(LF·CRLF 줄,
`/1` writer가 끊긴 줄에 붙여 쓴 줄, `/2` 줄, 모르는 가족 줄, 끊긴 마지막 줄, 한국어·NFD 이름, 빈 파일, 2 MiB 넘는 파일, 읽기 전용 파일, `.lab/`의 이력·로그·
캐시·잠금, 복사하지 않는 `.venv/`·`report/`·`__pycache__/`·`.DS_Store`, 옛 SDK 이름의 스크립트, 밖을 가리키는 폴더·파일 링크, 다시 만드는 안쪽 링크, 깨진
안쪽 링크, 루트와 바깥 폴더를 절대 경로로 기록한 옛 kit의 run)을 자식 프로세스에서 전환하다 그 단계에서 `os._exit(9)`로 끝내고
(`tests/migration_driver.py`), 남은 목적지를 판정한 뒤 같은 전환을 이 프로세스에서 다시 부릅니다. 기대: 표식 전에는 `incomplete`이고 kit이 읽기까지
`migration_incomplete`로 거절하며 옛 kit의 찾기(`lab.json`만)는 아무것도 찾지 않음, 다시 부르면 중단 없는 전환과 같은 사본(전환 디렉터리·표식·
`.gitignore` 머리 줄의 전환 id 제외), 원본 트리(독립 걷기, 잠금 파일 둘 제외) 그대로, 표식이 보고서를, 보고서가 매핑과 모든 사본을 묶음, 임시 파일
없음. 아래 "남은 것"·"다시 부른 결과"는 같은 행들을 run 폴더의 `fault_observe.py`가 헤드에서 다시 돌려 적은 것이고, 시험 결과는 개발 기기에서 모두
통과입니다. CI에서는 Linux·호스트 macOS가 37행 모두, Windows가 35행을 통과했고 F6·F12의 link와 unlink 사이 두 행은 Windows가 rename으로
게시해 건너뜁니다(JSON의 `fault_matrix.legs`).

| 지점 | 끊은 단계 | 남은 것(마지막 ledger 줄, 임시 파일) | kit의 읽기 | 다시 부른 결과 |
|---|---|---|---|---|
| F1 | `.analytics/`·잠금 뒤 / 전환 디렉터리만 / `start` 줄을 쓰다 끊김 | 전환 디렉터리 없음 / 빈 디렉터리 / 끊긴 첫 줄 | `migration_incomplete` | 새 전환(내용 없는 디렉터리는 지움), 같은 사본 |
| F2 | `start` 뒤 / 시험 파일 뒤 | `start` | `migration_incomplete` | 시험 파일을 지우고 판정부터, 같은 사본 |
| F3 | `locked` 뒤 | `locked` | `migration_incomplete` | 같음 |
| F4 | 계획 파일을 쓴 뒤 줄 전 / `plan` 뒤 | `locked` / `plan` | `migration_incomplete` | 다시 걷고 계획부터, 같은 사본 |
| F5 | `begin` 뒤 / 임시 파일 일부 / 임시 파일 전부 | `begin`, `data/raw/.mig-1f1a3024-0.part`(뒤 둘) | `migration_incomplete` | 그 경로를 다시 복사해 게시한 뒤 임시 파일을 지움 |
| F6 | 게시 뒤 `copied` 전 / POSIX link와 unlink 사이 | `begin`(뒤는 임시 이름도) | `migration_incomplete` | 지금 원본과 해시가 같아 사본으로 인정, 임시 이름은 지움 |
| F7 | 원본 재확인 중 | `copied` | `migration_incomplete` | 원본 잠금을 다시 잡고 이어 씀 |
| F8 | `source_checked` 뒤 / `verified` 뒤 | 그 줄 | `migration_incomplete` | 8단계부터, 원본을 읽지 않음 |
| F9 | `transform_begin` 뒤 임시 파일 전 / 임시 파일 빈 채 / 다 씀 / 교체 뒤 | `transform_begin`, `.mig-gitignore-0.part`(가운데 둘) | `migration_incomplete` | 임시 파일을 지우고 해시로 판정해 변환부터 또는 매핑부터 |
| F10 | 매핑을 쓴 뒤 / 보고서를 쓴 뒤 줄 전 / `reported` 뒤 / `durable` 뒤 | `transformed`·`reported`·`durable` | `migration_incomplete` | 보고서가 마지막 `reported`와 다르면 다시 쓰고 잇기 |
| F11 | `marker_begin` 뒤 임시 파일 전 / 빈 채 / 다 씀 | `marker_begin`, `.analytics-json-0.part`(뒤 둘) | `migration_incomplete` | 임시 파일을 지우고 같은 바이트의 표식부터 |
| F12 | 표식 게시 뒤 `committed` 전 / POSIX link와 unlink 사이 | `marker_begin`(뒤는 표식과 같은 inode의 임시 이름도) | 읽음(`current`) | `already_migrated`, `committed`를 쓰고, 같은 inode의 임시 이름은 지움 |
| F13 | `committed` 뒤 신뢰 전 | `committed` | 읽음 | `already_migrated` + 신뢰 추가 |
| F14 | `trusted` 뒤 | `trusted` | 읽음 | `already_migrated` |
| F15 | 링크가 없는 파일 시스템(포트가 링크를 거절)의 직접 쓰기에서 `direct_intent` 뒤 파일 전 / 파일을 만든 뒤 `direct` 전 | `direct_intent`, 임시 파일 | `migration_incomplete` | 이어 씀 / `destination_mismatch(occupied)`, 그 파일을 지우지 않음(R20). 표식도 그런 파일 시스템에서는 직접 쓰여, 표식을 만든 뒤 끊기면(`direct` 줄 전이든 뒤든) 빈·반쯤 쓴 `analytics.json`이 남고 다시 부르면 `marker_invalid`입니다(설계 6.9절이 표식 판정을 먼저 함). 소유자 질문으로 남김(아래 "실행하지 않은 것", PR 글) |
| F16 | `copied` 줄을 쓰다 끊김 / `source_checked` 줄을 쓰다 / `reported` 줄을 쓰다 / `committed` 줄을 쓰다 | 앞 줄의 상태 | 앞 줄과 같음 | 끊긴 줄을 건너뛰고 앞 줄의 상태로 이어 씀(마지막은 `already_migrated`) |

모든 행에서 원본 트리는 끊긴 뒤와 다시 부른 뒤 모두 그대로였고, 다시 부른 뒤 임시 파일은 없었습니다(F15의 둘째는 거절이라 남은 것 그대로). 원본에
잠금 파일이 없던 경우 `start`가 그 사실을, `locked`가 새로 만든 것을 적습니다(`assert_locks_named`).

설계 6.10절의 행(T6의 a–o)도 모두 통과입니다: (a) 이미 복사한 파일이 두 호출 사이에 바뀌면 `source_changed`이고 아무것도 지우지 않음(목적지 트리
그대로. 시험은 ledger를 빼고 비교하고, 그 시도가 ledger에도 쓰지 않은 것은 run 폴더의 확인 스크립트 `check_a.py`로 봄), (b) 아직 복사하지 않은 파일의 변경과 새 파일은 새 계획으로 복사, (c) `source_checked` 뒤 원본
journal에 덧붙임·원본을 지우고 원본 없이 부름·바깥 링크의 대상을 치움·빈 공간을 1바이트로 둠 → 원본 쪽 검사 없이 이어 커밋하고 매핑이 중단 없는
전환과 같으며 다시 부르면 덧붙인 줄(seq 2–2)을 셈, (d) `.gitignore`를 다른 내용으로 바꾸면 `destination_mismatch`, (e)(f) 커밋 뒤 표식을 지우면(원본도
없이) 같은 표식 바이트를 다시 게시하고 사본을 고치면 거절, (g) 순서가 맞지 않는 ledger·ledger 둘·JSON이지만 모르는 줄은 `destination_mismatch`이고
트리 그대로, (h) 원본에서 사라진 경로의 임시 파일은 지우지 않고 `leftover_temp`, (i) 계획에 없는 파일·고친 사본·계획에 없는 폴더는 거절, (j) `.venv/`·
`.DS_Store`·`Thumbs.db`·`__pycache__/`는 무시(OQ16), (k) `start` 줄 없는·빈·첫 줄이 끊긴 전환 디렉터리는 새 전환, (l) F9·F11의 임시 파일 행, (m) F15,
(n) 끊긴 줄 뒤에는 LF를 먼저 씀, (o) 폴더와 파일이 모두 읽기 전용인 원본은 잠금 없이(`locks: not_taken`, `PermissionError`) 복사하고 원본에 아무것도
만들지 않으며 목적지 루트·`.analytics/`는 쓸 수 있음(POSIX, root 아님).

리뷰 뒤 더한 행도 모두 통과입니다: `source_checked` 뒤 사본 하나를 지우고 원본 없이 이어 부르거나 같은 호출의 8단계 전에 지우면
`destination_mismatch(unaccounted)`(`details.missing`)이고 목적지 트리 그대로(dry run도 `state: refused`), 읽기 전용 폴더의 파일을 복사하다
끊긴(F5) 목적지는 다시 부르면 옛 임시 파일을 지운 뒤 폴더 권한을 돌려 커밋, `durable` 뒤 매핑을 지우고 `report.md`를 고치면 다시 부를 때 보고서와
그 파일을 다시 써 새 `reported`, 커밋 뒤 표식과 매핑을 지우면 매핑을 같은 바이트로 다시 만들고 표식을 다시 게시, `begin` 줄이 준 임시 이름을
다른 것이 먼저 만들면 그 이름에서 `occupied`이고 그 파일을 지우지 않음(다시 불러도 같음, 치우면 커밋), `.analytics`·`.analytics/migration`·전환
디렉터리·ledger가 원본으로의 링크이면 `destination_invalid(managed_link)`이고 원본·목적지 그대로, 자기 디렉터리의 `mapping.json.tmp`와 사본의 임시
이름에 원본의 읽기 전용 파일로의 링크를 심어도 링크만 지우고 커밋하며 원본의 바이트·권한 그대로, 원본 루트에 `.mig-gitignore-0.part`·
`.analytics-json-0.part`라는 파일이 있어도 한 번에 커밋하고 다시 부르면 모두 `same`.

수정 diff 확인 뒤 더한 행도 통과입니다: `source_checked` 뒤 사본의 빈 폴더를 지우고 원본 없이 이어 부르면 `destination_mismatch(unaccounted)`
(`details.missing: ["emptydir"]`)이고 목적지 트리 그대로(dry run도 `state: refused`), 커밋한 목적지의 표식이 묶은 보고서가 다른 전환의 바이트이거나
보고서가 묶은 매핑이 다른 전환의 바이트이면(각각 sha256을 맞춰 둠) 다시 부르기와 dry run이 아무것도 쓰지 않고 `destination_mismatch(invalid)`이고
읽기도 매핑을 쓰지 않음(`invalid`).

## 동시성(`concurrency`, T5·T7·T15·T19)

| 경우 | 결과 |
|---|---|
| 복사 중 잠금 없이 쓰는 옛 쓰기(사본이 만들어진 뒤 `telemetry.jsonl` 덧붙임, 파일 추가, 아직 복사하지 않은 파일 삭제, 재확인 직전 스크립트 편집) | 넷 모두 `source_changed`와 그 경로, 표식 없음, ledger 마지막 줄 `failed` |
| 옛 kit의 `record`(journal 잠금을 기다림)가 계획 뒤에 옴 | 원본 잠금을 놓을 때까지(7단계) 기다렸다가 원본에 쓰임, 사본에는 없음, 다시 부르면 그 줄 하나를 셈 |
| 같은 목적지로 두 전환(하나를 계획 뒤에 붙잡아 둠) | 둘째는 `destination_busy`, 같은 원본에서 다른 목적지로 간 셋째는 `source_busy`, 놓은 뒤 첫째가 커밋 |
| 다른 프로세스가 원본의 run 잠금을 쥠 | 즉시(10초 안) `source_busy`(`details.lock: .lab/run.lock`), 목적지는 `incomplete`로 남고 놓은 뒤 같은 호출로 커밋 |
| 다른 프로세스가 `exclusive`나 `try_exclusive`로 잠금을 쥠(포트) | `try_exclusive`는 기다리지 않고 `LockBusy`(5초 안), `exclusive`는 기다렸다가 놓은 뒤 얻음(POSIX. Windows는 그 레그) |
| 게시 직전 다른 프로세스가 계획 경로(복사본, 표식)에 파일을 만듦 | `destination_mismatch(occupied)`, 그 파일 바이트 그대로, 임시 파일은 ledger가 적은 채 남음. 다시 부르면 복사본은 같은 거절, 표식 자리는 그 파일이 표식이 아니라 `marker_invalid` |
| 전환 중 계획한 폴더 자리에 링크를 만듦 | `destination_mismatch(occupied)`, 링크의 대상에 아무것도 쓰지 않음 |
| 원본 재확인 직전 바깥 링크를 지움 / 다른 폴더로 바꿈 / 빈 폴더를 더함(리뷰 뒤) | 셋 모두 `source_changed`와 그 경로, 표식 없음 |
| `begin` 줄 뒤 다른 프로세스가 그 임시 이름에 파일을 만듦(리뷰 뒤) | `destination_mismatch(occupied)`(그 임시 이름), 그 파일 그대로, `failed` 줄이 이 전환의 것이 아님을 남김 |

## 규모와 단계(`fixtures`·`phases`·`sizes`·`preservation`)

`tools/eval_fixtures.py scale --scale S|M --seed 0`을 잰 코드(kit 0.7.0이 `lab.json`·`lab.*/1`을 씀)로 새로 만들고(기록의 시각 값 때문에
만들 때마다 몇 바이트 다름: 이번 S는 앞 측정의 2,898,447 B에서 2,898,446 B, M은 223,109,251 B로 앞 측정과 같음), 각 원본을 새 목적지에 두 번 전환했습니다
(자식 프로세스마다 하나, 시간은 ledger 줄의 시각에서, 두 번의 최솟값). 원본은 측정 스크립트의 걷기(전환 코드가 아닌 것)로 첫 전환 전과 둘째 전환·다시
부르기 뒤에 비교했습니다.

| 항목 | S | M |
|---|---|---|
| 원본 파일·바이트(잠금 파일 둘 제외) | 229개, 2,898,446 B | 2,037개, 223,109,251 B |
| 복사한 파일·바이트(`lab.json`의 바이트는 `source-marker.json`으로, `.lab/report.lock`은 뺌) | 228개, 2,898,446 B | 2,036개, 223,109,251 B |
| 복사하지 않은 것 | `.lab`의 잠금 셋, 빈 `.lab/cache`·`report`(0 B) | 같음 |
| 계획 / 복사 / 원본 재확인 / 사본 검증(ms) | 10 / 923 / 20 / 10 | 57 / 8,759 / 283 / 184 |
| `.gitignore` 변환 / 보고서 / flush / 표식 / 전체(ms) | 9 / 21 / 2 / 4 / 1,005 | 25 / 34 / 20 / 8 / 9,395 |
| 원본 잠금을 쥔 시간(ms) | 956 | 9,119 |
| 자식의 최대 RSS(시작 때) | 29.4 MB(17.3 MB) | 73.0 MB(17.3 MB) |
| `report.json` / `plan.json` / `mapping.json` / ledger | 47 KB / 148 KB / 833 B / 97 KB(469줄) | 395 KB / 1.36 MB / 833 B / 862 KB(4,085줄) |
| 원본 digest 전후 | 같음 | 같음 |
| 두 전환의 사본 | 같음 | 같음 |
| 다시 부르기 | `already_migrated`, 228개 모두 `same`, 원본의 새 줄 0 | `already_migrated`, 2,036개 모두 `same`, 원본의 새 줄 0 |

복사 시간의 대부분은 파일마다의 내구 쓰기(macOS의 `F_FULLFSYNC`)와 ledger 두 줄이고, 원본 잠금을 쥐는 시간은 거의 복사 시간입니다(설계 R1: 그동안 옛
aelix의 hook·도구가 journal 잠금에서 기다림). 두 fixture의 원본에는 잠금 파일이 이미 있어 새로 만든 것이 없습니다. 링크·옛 kit의 run은 이 fixture에
없으므로(모든 run이 0.7.0의 것) 그 경우는 단위 시험(T11, 아래)과 위 F 표의 원본이 봅니다.

## 기록 비교(`reader_comparison`, T11)

원본과 목적지를 `core.read_model`로 읽어 판정을 모두 비교했습니다(루트의 철자는 정규화): 참조 판정, run의 기록 상태, 기록 상태 재생, `unsupported`,
끊긴·복구한 줄, 계보, 가져오기, 모든 run 입력의 생산자.

| fixture | 판정 수 | 같음 |
|---|---|---|
| S | 참조 477, run 30, 기록 상태 252, 계보 39, 생산자 39, 가져오기 5, 끊긴 줄 4 | 모두 같음, 목적지의 매핑 적용(`applied: true`) |
| M | 참조 15,000, run 300, 기록 상태 9,633, 계보 483, 생산자 483, 가져오기 20, 끊긴 줄 4 | 모두 같음, 매핑 적용, 읽기 0.34초 |

`tests/test_migration_reader.py`가 옛 kit의 경로를 봅니다(심볼릭 링크, Windows 레그는 junction도): 옛 run이 루트를 다른 철자로 기록한 절대 경로,
`data -> /ext/data` 경계를 지나 실제 경로로 기록한 입력, 상대 `.lab/…` 경로, 같은 글자를 기록한 0.5.0 run. 목적지의 판정이 원본과 같고(다른 것은 `.lab/…`
경로에 매핑만이 주는 `read_as: .analytics/…` 하나), 목적지를 옮기고 `/ext/data`를 지운 뒤에도 같으며, 보고서의 바이트가 바뀌면 매핑을 쓰지 않고
`status`가 `report_changed`를 보입니다. 리뷰 뒤에는 매핑을 지우면 `source_revision`이 바뀌고 같은 바이트로 되돌리면 처음 값으로 돌아오는 것,
표식·보고서·매핑의 전환 id가 다르거나 보고서가 다른 전환의 디렉터리에 있으면 매핑을 쓰지 않는 것(`invalid`)도 봅니다. 수정 diff 확인 뒤에는
같은 목적지를 다시 부른 전환도 `destination_mismatch(invalid)`로 거절하는 것을 함께 봅니다(위 "중단과 이어 쓰기"). Windows 수정 뒤에는 매핑이
절대 경로를 원본의 읽기(`legacy_identity`)처럼 `normpath`로 읽는 것을 봅니다: `..`가 든 경로(모든 레그)와 두 구분자가 섞인 경로(Windows 레그;
옛 run이 기록한 `C:\…\src/tables/t.parquet`를 목적지도 `tables/t.parquet`로 읽음).

## 실행하지 않은 것

JSON의 `not_run`에 있습니다: Windows에서 원본을 읽는 중에 만난 다른 프로그램의 바이트 범위 잠금(C 런타임의 읽기가 Windows
오류를 넘기지 않아 `in_use`가 아니라 `permission`; 코드에서 읽음), 표식 뒤의 전원 손실과 Windows의 디렉터리 flush, FAT·exFAT 목적지(링크를 거절하는 포트로만
흉내 냄), 네트워크 파일 시스템·Linux casefold 디렉터리·Windows의 디렉터리별 대소문자 구별 상속, Linux에서 만든 예약 이름 원본을 Windows로 전환,
다른 드라이브·볼륨의 목적지, 명령 `analytics migrate`와 런처(셋째 PR)·실제 aelix 세션, 읽기 전용으로 마운트한 볼륨(읽기 전용 폴더 트리로 흉내 냄),
M의 Workspace 페이지 비교(판정만 비교함). 링크 없는 목적지의 표식 직접 쓰기가 끊긴 경우(F15 행의 표식)는 실제 매체가 아니라 링크를 거절하는
포트로만 보았고, 그 결과(`marker_invalid`)를 바꾸는 일은 소유자 질문입니다(권장: 있는 곳에서는 없을 때만 이름을 주는 rename — Linux
`renameat2(RENAME_NOREPLACE)`, macOS `renamex_np(RENAME_EXCL)` — 으로 게시하고 없으면 표식 전에 거절).

## 리뷰 뒤 다시 잼

리뷰 뒤 측정(코드 `80a0fc9`)을 처음 측정(코드 `2b6eabb`, 이 PR의 커밋 `f7faea5`가 담은 기록)과 비교하면, F
표의 37행은 남은 것·kit의 읽기·다시 부른 결과·사본·원본 비교가 모두 같고, 규모의 값은 같은 기기의 부하 안에서 비슷합니다(S 전체
1,025 → 1,011 ms, M 9,315 → 9,030 ms, M의 최대 RSS 69.8 → 74.1 MB). 저장소 시험은 리뷰에
더한 시험만큼 늘었습니다(위 결과 표). 리뷰에 더한 시험 23개는 앞 헤드 `f7faea5`의 코드에서 모두 실패하고(그 헤드의 시험
110개는 통과), 리뷰 수정 커밋에서 모두 통과합니다(JSON의 `new_tests_on_previous_head`).

## 수정 diff 확인 뒤 다시 잼

리뷰 뒤 측정(코드 `80a0fc9`, 그 커밋이 담은 기록)과 비교하면, F 표의 37행은 남은 것·kit의 읽기·다시 부른
결과·사본·원본 비교가 모두 같고(JSON의 `review_fix_measurement.fault_observed_rows_same`),
규모의 값은 같은 기기의 부하 안에서 비슷합니다(S 전체 1,011 → 1,065 ms, M 9,030 → 8,959 ms, M의 최대
RSS 74.1 → 72.3 MB). 보고서·계획·매핑·ledger의 크기와 줄 수는 같습니다. 저장소 시험은 수정 diff 확인에 더한
사례 3개만큼 늘었고, 그 3개는 `80a0fc9`의 코드에서 실패하고 이 커밋에서 통과합니다(JSON의
`new_tests_on_fix_check_head`).

## Windows CI 실패와 수정

PR #82의 앞 헤드 `53d1bdf`에서 CI의 Windows 레그(`platform · windows-2025`, run 37757801858, job 113246550243)만 사례 넷이 실패했습니다
(4 failed, 1,521 passed, 216 skipped; Linux·macOS 레그, 두 OS의 stdlib floor, lint는 통과). 실패 로그와
JUnit(`ci-platform-windows-2025`의 `suite.xml`, sha256은 JSON)에서 원인을 찾아 엔진을 고쳤고(`ebd10b6`), 시험이 말하는 보장은 그대로입니다.

| 실패한 사례 | 원인 | 수정 |
|---|---|---|
| `test_the_source_s_own_refusals`: `link_target_missing`의 링크 글자가 `\\?\C:\…`(시험은 링크를 만든 글자 `C:\…`) | 걷기가 링크의 글자를 `os.readlink`로 읽는데, Windows에서는 reparse point의 substitute name이라 절대 대상과 junction에 확장 길이 접두가 붙음 | 걷기가 글자를 접두 없이 둠(`_platform.plain_path`). 다시 만드는 링크는 상대 글자뿐이라 다시 만들기와 ledger의 비교는 그대로 |
| `test_a_source_file_another_program_holds_open_is_reported`: `reason`이 `permission`(시험은 `in_use`) | `os.open`은 C 런타임의 `_wopen`을 거치고, 그것은 공유 위반(WinError 32)을 errno `EACCES`로만 알려 OSError에 `winerror`가 없음 | 원본 파일을 `_platform.open_for_reading`으로 엶: Windows는 `_wopen`과 같은 접근·공유로 CreateFile을 불러 거절의 WinError를 지키고, 그 핸들의 C 런타임 descriptor를 씀. POSIX는 전과 같은 `os.open` |
| `test_the_reader_reads_a_migrated_project_as_it_read_its_source[symlink]`·`[junction]`: 옛 run이 기록한 `C:\…\src/tables/t.parquet`를 목적지가 읽지 못함(원본은 `tables/t.parquet`로 읽음) | 매핑이 절대 경로를 `os.sep`으로만 나눈 글자로 원본 루트의 철자와 비교함. 원본의 읽기는 `normpath`로 읽음 | 매핑이 절대 경로와 철자를 `legacy_identity`처럼 읽음(접두를 뗀 뒤 `normpath`). `..`도 원본의 읽기처럼 글자로 접음 |

이 기기에는 Windows가 없어 원인 셋은 로그와 CPython·Win32의 문서화된 동작으로 판단했습니다. 새 시험 가운데 macOS에서 도는 `..` 사례는 `53d1bdf`의
코드에서 실패하고 `ebd10b6`에서 통과합니다(위 결과 표). 나머지는 이 코드를 담은 헤드 `ec8d7d9`의 Windows 레그(run 37789500898, job
113352791801)에서 확인했습니다: 실패했던 네 사례와 두 구분자가 섞인 경로의 단언이 모두 통과했고, 레그 전체가 1,525 passed, 216 skipped,
0 failed입니다(JSON의 `windows_ci_failure.at_the_fixed_head`, 위 "CI의 레그"). 읽는 중에 만난
바이트 범위 잠금(WinError 33)은 C 런타임의 읽기가 여전히 errno로만 알려 `permission`이고, CONTRACTS G에 적었습니다.

## Windows 수정 뒤 다시 잼

수정 diff 확인 뒤 측정(코드 `84f3970`, 커밋 `53d1bdf`까지의 기록)과 비교하면, F 표의 37행은 남은 것·kit의 읽기·다시 부른
결과·사본·원본 비교가 모두 같고(JSON의 `fix_check_measurement.fault_observed_rows_same`), 규모의 값은 같은 기기의 부하 안에서 비슷합니다(S 전체
1,065 → 1,005 ms, M 8,959 → 9,395 ms,
M의 최대 RSS 72.3 → 73.0 MB). 계획·매핑·ledger의 크기와 줄 수는 같고, S의 보고서는 그 안의 원본 잠금을 쥔 시간
(`held_ms`)의 자릿수만큼 1바이트 작습니다. 저장소 시험의 수는 같습니다(새 단언은 기존 사례 안).
