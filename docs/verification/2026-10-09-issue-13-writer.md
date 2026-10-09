# Issue #13 새 writer·정책 전환·`analytics migrate` 측정(PR ③) — 2026-10-09

[#13](https://github.com/handochan/aelix-analytics/issues/13)의 셋째 구현 PR ③(kit 0.8.0)은 새 형식의 공통 writer(`writer.Writer`)로 모든 쓰기
진입점을 옮기고, 쓰는 형식을 `analytics.project/2`로 바꾸며(`lab.json` 프로젝트는 `migration_required`), 저장 이름을 새 배치로 바꾸고, 전환을
명령 `analytics migrate`로 열고, aelix 안의 작성 주체를 확장이 aelix 프로세스의 환경과 `user_bash` 처리로 밝힙니다. 기록 형식과 프로젝트 전환을
바꾸는 PR이라 운영 정책의 측정 기록이며, [설계](../design/RECORD-WRITER-AND-MIGRATION.md) 12절이 이 기록에 정한 키 `writer_latency`·
`entry_points`·명령 E2E(`command_e2e`)·`migration`(PR ②의 항목 다시 잼)·`uv`(T18)·`agent_cli`(T22)가 있습니다. 모든 값은
[2026-10-09-issue-13-writer.json](2026-10-09-issue-13-writer.json)에 있습니다. 계약에 반영한 것은 [CONTRACTS.md](../CONTRACTS.md) A–G와 F1의
kit 0.8.0 절입니다.

이 판은 구현 리뷰(독립 리뷰와 Codex 리뷰)의 지적을 고친 뒤 다시 잰 것입니다. 리뷰 전의 판(코드 `54e9098`)이 잰 writer 지연·전환·명령 E2E·T18·
T22를 다시 쟀고, 설계와 다르게 구현한 것(`departures`)과 fixture 재현성을 더했습니다. 리뷰 전 판의 값은 JSON의 `measured_tree.previous_record`와
`writer_latency.before_the_fix`가 가리킵니다.

그 뒤 [PR #84](https://github.com/handochan/aelix-analytics/pull/84)의 CI(헤드 `8d3695f`, 실행 37840617751·37840622273)가 시험 둘의 실패를
보였고, 소유자가 PR의 질문 넷에 답했습니다(2026-10-09, [#13](https://github.com/handochan/aelix-analytics/issues/13#issuecomment-6070315072)). 그 처리(코드 커밋 `9887509`) 뒤 바뀐 부분 — 전환
S·M과 중단 행렬, 명령 E2E, 진입점 행렬, T18, T22, FAT·exFAT, 검증 전체와 배포본 E2E — 를 다시 쟀습니다(아래 "소유자 결정 뒤 다시 잼").
값을 바꾼 키는 그 전 값을 옆에 두고(`before_owner_decisions`, `previous_verification`, `migrate_before_owner_decisions`), 다시 재지 않은 키(writer
지연, fixture 재현성)는 그 전 측정 그대로입니다.

그 뒤 소유자가 같은 날 결정 2·3을 보정했고([#13](https://github.com/handochan/aelix-analytics/issues/13#issuecomment-6072183477)), 그 전 판의 마지막 점검이 F1의 빈틈과 이 기록의 사소한 셋(manifest 개수, 없는 절 참조,
CI 칸의 커밋)을 짚었습니다. 그 처리(코드 커밋 `eaf6dfe`) 뒤 코드가 닿는 부분 — T22, 진입점, AppleDouble 규칙이 닿는 전환 행(중단 행렬과 그
AppleDouble 행), FAT32·exFAT, 검증 전체와 배포본 E2E, 새 시험의 `d0c766b` 실행 — 을 다시 쟀습니다(아래 각 절의 "보정 뒤"). 그 전 값은 JSON의
`*_before_corrected_decisions`에 그대로 두었고, 다시 재지 않은 키(writer 지연, 전환 S·M의 시간·크기·보존·기록 비교, 명령 E2E, T18, fixture
재현성)는 그 전 측정 그대로입니다(`measured_tree.after_corrected_decisions.kept`).

그 뒤 `0504cc8`의 점검이 결정 3·2의 구현이 결정보다 넓은 것을 여덟 짚었습니다(요지: `user_bash` 판정이 줄을 POSIX 셸로만 읽어 aelix가 실제로 그
줄을 돌릴 셸이 둘째 명령으로 읽는 줄을 kit 명령 하나로 봄, 목적지의 AppleDouble 파일이 복사 경로일 때의 `occupied`, 지우지 못한 전환 디렉터리의
hint). 그 처리(코드 커밋 `fix: answer the check of the corrected decisions`) 뒤 코드가 닿는 부분 — T22(와 `$'…'` 줄), 실제 셸로 돌리는 새 함정
시험(`user_bash_shell_traps`), 진입점, 중단 행렬, AppleDouble 규칙, FAT32·exFAT, 검증 전체와 배포본 E2E, 새 시험의 `0504cc8` 실행 — 을 다시
쟀습니다(아래 각 절의 "점검 뒤"). 그 전 값은 JSON의 `*_after_corrected_decisions`·`*_before…`에 그대로 두었고, 다시 재지 않은 키(writer 지연,
전환 S·M, 명령 E2E, T18, fixture 재현성)는 그 전 측정 그대로입니다(`measured_tree.after_check_of_corrected_decisions.kept`).

그 뒤 `221dfbb`의 점검(POSIX 셸과 PowerShell 두 갈래)이 `user_bash` 판정에 지적 다섯을 냈습니다(꼭 하나: Windows에서 맨 이름이 배치 파일로 풀리면
cmd가 인자를 다시 읽음). 그 처리(코드 커밋 `fix: leave more lines alone in the person's-shell judgment`)는 판정만 좁혔으므로 그 코드가 닿는 부분 —
aelix 레그(T22와 함정 시험), 저장소 시험, 문서 검사, 두 판정의 비교 — 만 다시 쟀습니다(`measured_tree.after_the_221dfbb_checks`). T22는
`221dfbb`의 값과 같아 그 값을 두었고(`agent_cli.same_after_the_221dfbb_checks`), 함정 시험의 그 전 값은 `user_bash_shell_traps.observations_at_221dfbb`에
두었으며, 나머지 키는 그 전 측정 그대로입니다.

이 기록의 값은 모두 개발 기기(macOS)의 것입니다. `8d3695f`의 CI는 위 실패로 끝났고(`runs.at_8d3695f`), `221dfbb`는 push되어 이 판의 커밋을
만들 때 CI가 돌고 있었으며(결과는 이 기록에서 읽지 않음), 이 판이 잰 코드는 아직 push하지 않아 CI 칸(`runs.code`·`legs`)은 `pending`입니다.

## 출처와 환경

| 항목 | 근거 |
|---|---|
| 다시 잰 코드(`221dfbb` 점검 뒤) | 커밋 `fix: leave more lines alone in the person's-shell judgment`(`221dfbb` 위). 커밋 전 worktree에서 쟀고 그 커밋의 코드 파일은 재고 manifest(`measured_tree.after_the_221dfbb_checks`)의 것입니다 |
| 다시 잰 코드(점검 뒤) | 커밋 `fix: answer the check of the corrected decisions`(`0504cc8` 위). 커밋 전 worktree에서 쟀고 그 커밋의 코드 파일은 재고 manifest(219개)의 것입니다 |
| 다시 잰 코드(보정 뒤) | 커밋 `eaf6dfe`(fix: run only a single kit command in a person's shell and count AppleDouble files only, `d0c766b` 위). 커밋 전 worktree에서 쟀고 그 커밋과 파일 manifest(219개, sha256 `f49c1ac8…`)가 같습니다 |
| 다시 잰 코드(소유자 결정 뒤) | 커밋 `9887509`(fix: answer the CI and the owner's decisions of PR 3, `8d3695f` 위). 커밋 전 worktree에서 쟀고 그 커밋과 파일 manifest(219개, sha256 `fa8a7c5a…`)가 같습니다 |
| 측정한 코드 | `feat/issue-13-new-writer`의 커밋 여섯(writer → 진입점·저장 이름 → 확장의 작성 주체 → 명령 → 문서 → 첫 측정 기록, 헤드 `ceaf736`; 리뷰가 작성 주체 커밋을 명령 커밋 앞으로 옮김) 위에 리뷰 수정을 커밋하기 전의 worktree. 리뷰 수정 커밋이 잰 코드와 이 기록을 담습니다. `origin/main` `0f3f09f`(PR #82 병합) 위입니다. JSON의 `measured_tree`는 검증 때 만든 파일 manifest(추적하는 `src`·`extension`·`tests`·`tools`·`bin`·`pyproject.toml`·`uv.lock` 파일마다 `shasum -a 256` 한 줄, 경로 순, 219개)의 sha256을 담고, 목록은 run 폴더에 있습니다 |
| 비교한 기준 | writer 지연과 fixture는 PR ③ 전의 kit(`ebd10b6`, `tools/eval_fixtures.py`의 `V1_KIT_COMMIT`; PR #82의 헤드로 main에 있음)의 `src/`를 `git archive`로 꺼내 썼습니다. 리뷰 수정의 시험은 수정 전 코드 `f42a9a3`에서 돌렸습니다(아래) |
| 워크플로 | `.github/workflows/ci.yml`: aelix 레그가 `tests/test_agent_cli.py`도 돌리고 관찰을 `evidence-aelix-py3.12/`·`3.11/`에 남깁니다(이 PR의 변경) |
| 코드 실행(CI) | 실행하지 않음: 이 판이 잰 코드 `eaf6dfe`는 push 전입니다(마지막 CI는 `8d3695f`의 것, `runs.at_8d3695f`) |
| 개발 기기 | macOS 26.6.2 arm64(Apple M5 Pro), uv 0.11.19. 저장소 시험 CPython 3.12.13(`uv.lock`의 개발 환경), aelix 레그 3.12.13·3.11.15, 런처·stdlib 스모크 3.11 |
| 격리 | `AELIX_ANALYTICS_TRUST_FILE`·`AELIX_ANALYTICS_UV`를 run 폴더 안으로(`uv`는 실제 uv로의 링크, 측정은 따로 둔 신뢰 목록), `LAB_*`·`AELIX_LAB_*` 없음. 잰 명령은 `~/.aelix`를 읽거나 쓰지 않습니다. T22는 aelix를 시험의 임시 디렉터리를 HOME·`AELIX_CODING_AGENT_DIR`로 한 채 로드합니다(모델·네트워크·로그인 없음). T18의 `~` 사례와 명령 E2E의 `~` 단계는 HOME을 시험·run의 디렉터리로 두고 uv의 캐시·Python은 그대로 씁니다 |
| 격리(소유자 결정 뒤) | 위와 같고, HOME을 run 폴더의 가짜 디렉터리로 두었습니다(uv의 캐시·Python 디렉터리만 실제 값). 이 판에서 `~/.aelix`를 읽거나 나열하거나 쓰지 않았습니다 |
| 격리(보정 뒤) | 같은 격리를 이 판의 run 폴더 `corrected/`에 새로 두었습니다(신뢰 목록, 가짜 HOME). 이 판에서 `~/.aelix`를 읽거나 나열하거나 stat하거나 쓰지 않았습니다 |
| 격리(점검 뒤) | 같은 격리를 run 폴더 `round5/`에 두었습니다(신뢰 목록, 가짜 HOME). 판정을 실제 셸로 돌려 본 탐색은 scratchpad에서 했고, `~/.aelix`를 읽거나 나열하거나 stat하거나 쓰지 않았습니다 |
| 격리(`221dfbb` 점검 뒤) | 같은 격리를 run 폴더 `round6/`에 두었습니다(신뢰 목록, 가짜 HOME). PowerShell의 파서로 프로그램 단어를 읽어 본 것(실행 없음)과 `-c/analytics` stand-in 실행은 run 폴더·scratchpad에서 했고, `~/.aelix`를 읽거나 나열하거나 stat하거나 쓰지 않았습니다 |
| 격리 사고 | 구현 중(G4, 리뷰 전) 데모 프로젝트를 살피던 명령 하나에 `ls ~/.aelix/agent 2>/dev/null \| head -0`이 실수로 들어가 실행됐습니다. 출력은 보지 않고 버려졌고 아무것도 쓰지 않았습니다(run 폴더의 handoff와 PR 본문에 적음). 잰 명령은 `~/.aelix`를 읽거나 나열하지 않았습니다 |

## 결과

보정한 결정의 점검을 처리한 판(코드 커밋 `fix: answer the check of the corrected decisions`, `0504cc8` 위)입니다. 그 전 판들의 결과는 JSON
`verification_after_corrected_decisions`(코드 `eaf6dfe`: 저장소 시험 1,963 passed·107 skipped, aelix 레그 303/303, 런처 196 passed 등),
`verification_before_corrected_decisions`(코드 `9887509`)와 `previous_verification`(리뷰 수정 판)에 있습니다. 아래 표는 이 판의 값입니다.

| 검사 | 명령 | 결과 |
|---|---|---|
| ruff | `uv run --locked ruff check tests extension src tools` | 통과 |
| 저장소 시험 | `uv run --locked pytest -q -ra -p no:cacheprovider --timeout 600 --timeout-method=thread` | 통과: 1,966 passed, 107 skipped(842초). 건너뜀은 Windows 전용·`AELIX_ANALYTICS_TEST_INSTALL`·Linux 전용·opt-in FAT·exFAT 사례와, aelix가 없는 개발 환경에서 건너뛰는 aelix 시험 파일 셋 |
| 배포본 E2E(CI의 변수) | `CI=1 AELIX_ANALYTICS_TEST_INSTALL=1 … pytest tests/test_packaging.py tests/test_windows_console.py tests/test_windows_long_paths.py` | 통과: 26 passed, 28 skipped(Windows 전용 둘은 건너뜀) |
| 계층·포트 계약 | `tests/test_layers.py tests/test_contracts.py` | 통과: 126 passed, 52 skipped |
| aelix 0.1.0b2, CPython 3.12 / 3.11 | `tests/test_extension.py tests/test_usage_report.py tests/test_agent_cli.py`(CONTRIBUTING의 격리 환경) | 통과: 776 passed / 776 passed(셸 군마다의 판정 표와 함정 시험을 더해 늘었습니다) |
| 런처, CPython 3.11 | `tests/test_launcher.py` | 통과: 196 passed, 1 skipped(Windows) |
| 사용량 보고서 | `tools/usage_report.py`(aelix 0.1.0b2, 3.12) | 필수 사실 58/58, 불변식 42/42. 그 전 판(`eaf6dfe`)의 보고서와 텍스트 76개를 비교해(임시 디렉터리·`source_revision`·aelix의 날짜 줄·가짜 HOME의 폴더 이름을 맞춤) 다른 텍스트 없음. 다른 숫자는 aelix 기본 프롬프트의 크기뿐(가짜 HOME의 폴더 이름 `round5/home`이 그 전 `corrected/home`보다 3자 짧음): 확장이 모델에 주는 글은 같습니다 |
| 플랫폼 스모크 | `tools/platform_smoke.py --gate required` / `--stdlib-only`(3.11) | holds 25, violated 0 / holds 15, violated 0, not_run 2(이 플랫폼에서 필수 아님) |
| 문서 검사 | `tools/check_docs.py` | `ok: true`, 오류 0(Markdown 66, 로컬 링크 729) |
| `git diff --check origin/main` | 작업 트리 | 출력 없음 |
| opt-in FAT32·exFAT | `AELIX_ANALYTICS_TEST_FOREIGN_FS=<디스크 이미지 둘>`, `tests/test_migrate_command.py -k another_kind` | 통과: 4 passed, 51 deselected(전환 시험과 보정한 AppleDouble 규칙의 시험, 두 볼륨; 아래 `foreign_fs`) |
| 새 시험을 `0504cc8`의 코드에서 | 이 판이 바꾼 `tests/test_migration.py`를 `0504cc8`의 worktree에 넣고(지웠음), 그리고 판정 표를 두 코드의 `is_single_kit_command`로 비교(줄을 돌리지 않음) | 전환 충돌 시험 넷이 기대대로 실패(그 전 코드는 복사를 시작해 `occupied`로 끝남; `details.removed`). 판정 표: 그 밖의 줄 표(POSIX 28·PowerShell 28)가 그 전 코드에서 모두 kit 명령 하나로 받아지고, kit 명령 하나인 줄 표 셋(`python -X …`, `uv run`의 아는 옵션 둘)이 그 전엔 거부됨(`new_tests_on_0504cc8`) |
| 함정 시험(실제 셸) | `tests/test_extension.py::test_a_line_judged_one_kit_command_runs_no_other_command_in_each_shell_here` | 통과. 이 기기의 셸(sh·bash·zsh·dash·ksh·pwsh)로 함정 줄 중 kit 명령 하나로 판정한 것(POSIX 17, pwsh 4)을 실제로 돌려 둘째 명령이 돌지 않음(`user_bash_shell_traps`) |

이 판(점검 처리)의 결과입니다. G1–G4(리뷰 전)의 새 시험을 그 앞 코드에서 돌린 결과는 각 그룹의 run 폴더 증거에 있고 PR 본문이 적습니다.

**`221dfbb` 점검 뒤**(코드 커밋 `fix: leave more lines alone in the person's-shell judgment`, JSON `verification_after_the_221dfbb_checks`). 판정만
바뀌었으므로 그 판정이 닿는 검사만 다시 돌렸습니다. 위 표의 다른 행(배포본 E2E, 계층·포트, 런처, 사용량 보고서, 스모크, FAT·exFAT)은 `221dfbb`의
값입니다.

| 검사 | 명령 | 결과 |
|---|---|---|
| ruff | `uv run --locked ruff check tests extension src tools` | 통과 |
| aelix 0.1.0b2, CPython 3.12 / 3.11 | `tests/test_extension.py tests/test_usage_report.py tests/test_agent_cli.py`(CONTRIBUTING의 격리 환경) | 통과: 833 passed / 833 passed(Windows 표 둘, PowerShell 프로그램 단어 행, 함정 줄 하나를 더함) |
| 함정 시험(실제 셸) | 위와 같은 시험 | 통과. 함정 줄 29개 가운데 kit 명령 하나로 판정한 것(POSIX 18, pwsh 5)이 이 기기의 셸에서 둘째 명령을 돌리지 않음 |
| 저장소 시험 | `uv run --locked pytest -q -ra -p no:cacheprovider --timeout 600 --timeout-method=thread` | 통과: 1,966 passed, 107 skipped(845초) |
| 문서 검사 / `git diff --check origin/main` | `tools/check_docs.py` / 작업 트리 | `ok: true`, 오류 0(Markdown 66, 로컬 링크 729) / 출력 없음 |
| 판정 비교 | `221dfbb`의 `is_single_kit_command`와 이 판의 것을 표의 행·함정 줄·무작위 줄 37,128개에 셸 다섯과 Windows 여부로(줄을 돌리지 않음) | 새로 받는 줄 0. Windows 밖에서 새로 받지 않는 줄은 PowerShell 프로그램 단어 행 아홉(`judgment_after_the_221dfbb_checks`) |

## writer 지연(`writer_latency`, 11.5절)

10,000줄 journal에서 기록 하나의 비용입니다. 소유자 결정 뒤와 그 보정 뒤에는 다시 재지 않았습니다: 그 변경(`writer.project_above`·`inside`, init,
전환의 판정·제외, 확장의 `!` 줄 판정)은 기록 경로에 없습니다. 그 전 kit(`ebd10b6`, `/1` writer `core.add_note`)은 M 원본(`lab.json`)의 사본에, 이 트리(`core.record`·
`Writer.update`)는 그 원본을 전환한 목적지(`analytics.json`, 같은 10,000줄에 `/2` 줄을 덧붙임)의 사본에 썼습니다. 종류마다 20회, kit마다 자기
프로세스에서 그 kit만 import하고, 두 kit을 번갈아 두 번 돌려 판마다 새 사본을 썼습니다. 값은 두 판의 최솟값입니다(ms).

| 기록 | 그 전 kit p50 / p95 | 이 트리 p50 / p95 | 리뷰 전 판 p50 |
|---|---|---|---|
| 보통 기록(`note`) | 32.7 / 36.5 | 69.4 / 78.7 | 110.2 |
| 참조 넷을 이름으로 주는 기록(run id, 데이터셋 이름, 이벤트 id, `script.py::id`) | 32.3 / 37.4 | 124.1 / 135.7 | 152.0 |
| 열린 질문을 닫는 기록(`resolve`) | 65.2 / 74.7 | 128.9 / 146.2 | 127.5 |
| `record_update`(현재 finding을 철회, 잠금 안의 상태 재생) | — | 91.2 / 95.2 | 86.8 |

리뷰 수정 뒤 writer는 기록 하나에 journal 전체를 두 번 파싱합니다(`relations.Journal`): 잠금 밖(결박과 줄 크기)과 잠금 안(재판정과 상태
재생). 덧붙인 뒤의 셋째 파싱(응답의 `targets`)은 다른 기록의 상태를 바꾸는 참조(`resolves`·`supersedes`)가 있을 때만 합니다. 그래서 보통 기록과
참조를 주는 기록이 줄었고, 질문을 닫는 기록은 그대로입니다. 이름으로 준 참조가 있으면 읽기 모델도 읽습니다. kit CLI `record`와
`analytics_record`는 맥락 동기화 전에 잠금 밖 판정을 한 번 더 합니다(`core.check_record`, 아래 표에는 들지 않음: 표는 `core.record`를 직접 부름).
seq·상태 색인은 설계 11.5절대로 #15의 인덱스와 함께 둡니다. 이 값은 [현재 계약](../CONTRACTS.md) G의 "비용"에 적었습니다.

## 설계와 다르게 구현한 것(`departures`)

설계 머리말이 요구하는 대로 이 PR이 설계의 글자와 다르게 구현한 것입니다. 셋 다 채택된 보장을 좁히지 않습니다(설계 19.3절에도 적음).

| 설계 | 구현 | 까닭 |
|---|---|---|
| 4.7절: 이벤트당 참조 24개 상한(`MAX_EVENT_REFS`)을 둠 | `Writer`에 개수 상한이 없고 줄 상한(OQ2, 50 KiB)이 묶음 | 0.8.0 전에도 상한은 run 완료 이벤트의 데이터 참조에만 있었고(`add_note`에는 없음) 지금 그 이벤트는 run 참조 하나다. 거절로 되돌리면 설계에 필요 없는 거절을 더함 |
| 2.9절(OQ2): 본문은 상한까지 통째로, 자르지 않음 | kit이 만드는 본문(가져오기 이벤트·run 완료·프로젝트 생성)은 다른 곳에 통째로 남는 필드(가져오기 기록의 `description`, `run.json`의 `title`, 표식의 `title`)를 앞 4,000자까지 인용하고 `…`(`writer.excerpt`). run의 오류 요약은 `SystemExit`도 예외처럼 300자 | OQ2를 호출자가 기록하는 본문의 규칙으로 읽음. 통째로 담으면 데이터·`run.json`·표식을 쓴 뒤 kit 자신의 기록이 거절됐다(리뷰). 0.7.0은 4,000자로 잘라 기록함 |
| 4.3절: 스크립트의 거절은 예외 | run 안 SDK 기록은 부를 때 가장 넓은 seq(12자리, `writer.SEQ_BOUND`)로 줄을 판정. seq가 줄에 두 번(`seq`·`id`) 들어가므로 판정한 줄은 쓸 줄보다 2 × (12 − 실제 seq의 자릿수) bytes, 많아야 22 bytes 길고, 그만큼 상한에 가까운 줄은 run 밖이라면 쓰일 것도 거절(수정 diff 확인이 처음의 "24 bytes"를 고침) | run이 끝날 때 쓰는 기록의 seq는 부를 때 모름. 끝날 때 판정하면 넘는 기록이 스크립트가 계속된 뒤 사라졌다(리뷰) |

## 진입점 행렬(`entry_points`, T8)

시험이 남긴 그대로입니다(`tests/test_marker_gate.py`, aelix 레그의 `tests/test_extension.py`). 소유자 결정 뒤 다시 남긴 값에서 `migrate` 열의 두
칸이 바뀌었습니다(OQ9 넓힘): 표식 없는 전환 목적지 안의 목적지가 `inside_project`가 되었고, 표식 없음 열은 그 안의 목적지로 전환이
커밋함을 이제 시험이 확인합니다(그 전에는 확인하지 않음). 나머지 칸은 리뷰 전 판과 같습니다(그 전 값은 JSON `migrate_before_owner_decisions`). 칸이 비면 받아들여짐(쓰거나
읽음), 아니면 오류 코드이고, 거절·건너뜀인 칸은 프로젝트 트리(잠금 파일 포함)가 전후로 같습니다.

| 열 | kit CLI 쓰기(run·import·record·render·demo) | kit CLI 읽기(status·context·query) | SDK(run 밖) | 확장의 쓰기 도구·명령 | `migrate`(원본 / 그 안의 목적지) |
|---|---|---|---|---|---|
| `lab.project/1` | `migration_required` | 읽음 | `migration_required` | `migration_required` | 복사 / `inside_project` |
| 읽을 수 없는 `lab.json` | `migration_required` | 읽음 | — | — | 복사 / `inside_project` |
| `lab.project/9` | `project_too_new` | 읽음 | — | `project_too_new` | `source_not_legacy` / `inside_project` |
| `analytics.project/2` | 씀(데모는 `demo_not_empty`) | 읽음 | 씀 | 씀(행렬 밖의 확장 시험) | `source_not_legacy` / `inside_project` |
| `analytics.project/2`, 더 높은 `min_writer_version` | `writer_too_old` | 읽음 | `writer_too_old` | `writer_too_old` | `source_not_legacy` / `inside_project` |
| `analytics.project/3` | `project_too_new` | 읽음 | — | — | `source_not_legacy` / `inside_project` |
| 표식이 아닌 `analytics.json` | `marker_invalid` | 읽음 | `marker_invalid` | `marker_invalid` | `source_not_legacy` / `inside_project` |
| 두 표식 | `marker_conflict` | `marker_conflict` | `marker_conflict` | `marker_conflict`(읽기 포함) | `source_not_legacy` / `inside_project` |
| 표식 없음 | `no_project` | `no_project` | — | — | `no_project` / 복사 |
| 표식 없는 전환 목적지 | `migration_incomplete` | `migration_incomplete` | — | — | `source_not_legacy` / `inside_project`(소유자 결정 뒤) |
| `lab.json` 프로젝트 아래의 `analytics.json` 디렉터리 | 씀 | 읽음 | 씀 | — | `source_not_legacy` / `inside_project` |

"—"는 그 열을 그 진입점으로 시험하지 않은 칸입니다(JSON의 열 목록이 시험한 것). 리뷰 뒤 더한 시험: 프로젝트 안의 `init`·`demo DIR`을 두 런처가
kit의 init으로 거절하고 아무것도 만들지 않음(`analytics.json`·`lab.json` 위, `init_inside_a_project`), `analytics_init`·`/analytics init|demo`도
같음(aelix 레그), 데모가 쓴 모든 기록이 새 형식이고 기록 schema에 맞음. 런처·`bin/analytics`의 행, run 안 SDK의 표식 바뀜, journal·run 잠금 안의
재판정은 시험 이름으로 JSON에 있습니다.

소유자 결정 뒤 더한 시험(`entry_points.inside_a_migration_s_destination_not_committed`): 끊긴 전환의 목적지(잠금만, 전환 디렉터리만, 둘 다 / F1·F6)
아래로 init — core, kit CLI(밖에서와 그 안에서의 상대 경로), 두 런처의 `init`·`demo`, `analytics_init`·`/analytics init|demo` — 과 `migrate --to`(다른
원본, 같은 원본, `--dry-run`)가 모두 `destination_invalid(inside_project)`이고 `details.project`가 그 목적지이며 목적지·원본·신뢰 목록이 그대로이고,
그 뒤 첫 전환이 같은 명령으로 이어 커밋합니다. 모두 `8d3695f`의 코드에서는 실패합니다.

보정 뒤(`entry_points.after_corrected_decisions`): 행렬의 관찰을 다시 남겼고 모든 열이 그 전 판과 같습니다
(바뀐 init의 빈 폴더 판정은 행렬의 칸에 없음). 더한 시험: init이 `._`로 시작하는 폴더나 링크가 있는 폴더를 `init_not_empty`로 거절하고 그대로
두는 것, 확장의 `!` 줄 표 둘(aelix 레그, 아래 `agent_cli`).

## 명령 E2E(`command_e2e`)

S fixture의 사본에 자기 `uv.lock`과 `.venv/`를 만들어(사람의 프로젝트처럼) 두 런처(`bin/analytics`, 설치본 `analytics`의 코드)를 실제 uv로
불렀습니다. 두 런처의 결과가 같습니다. 소유자 결정 뒤 다시 잰 값이며 단계마다 그 전 판과 같은 결과입니다(시간은 기기 부하로 조금 김, 아래
"전환 다시 잼"; 그 전 값은 JSON `command_e2e.before_owner_decisions`).

| 단계 | 결과 |
|---|---|
| `migrate --to D --dry-run` | 0, `dry_run: true`, D를 만들지 않음 |
| `migrate --to D` | 0(1.6초), 목적지의 `.venv/`를 `uv sync --locked`로 만듦, 목적지의 `uv.lock`이 원본과 같은 바이트, stderr `analytics: the project is D now, and the source is as it was (in aelix: /analytics use D)` |
| 다시 `migrate --to D` | 0, `already_migrated: true`, 사본 229개 `same`, `differs` 없음 |
| D에서 `status` / `record finding e2e` | 0 / 0, 쓴 줄은 `analytics.event/2`·`human`·`inferred`·`cli` |
| 원본에서 `record finding e2e` | 1, `migration_required` |
| 원본 재확인 뒤 `os._exit`로 끊은 목적지 | `status`가 1·`migration_incomplete`, `.venv/` 없음 |
| 그 목적지로 `migrate` | 0, 이어서 커밋, `.venv/`를 만듦 |
| `migrate --to=~/tilde-<런처>`(셸이 `~`를 풀지 않은 그대로, HOME은 run의 디렉터리) | 0(1.7–1.8초), kit이 HOME 아래에 만든 목적지가 응답의 `destination`이고 그 `.venv/`를 만듦, 원본 안에 `~` 폴더 없음 |
| 원본(`.venv/`·`uv.lock` 포함 5,738개)의 트리 | 처음과 끝이 같음 |

## 전환 다시 잼(`migration`, PR ②의 항목)

S·M fixture는 리뷰 전 판의 측정이 이 브랜치의 `tools/eval_fixtures.py scale --scale S|M --seed 0 --kit-root <V1_KIT_COMMIT의 src>`로 만든 것을
그대로(복제해) 썼습니다(OQ7; S 229개·2.9 MB·journal 300줄, M 2,037개·223 MB·journal 10,000줄). 전환은 모두 명령 `python -m aelix_analytics
--project SRC migrate --to DEST`를 자기 프로세스로 부른 것입니다(런처가 uv 없이 부르는 것과 같음). 소유자 결정 뒤 다시 잰 값이며, 같은 fixture를
복제해 썼습니다(`tree_digest`가 그 전 판과 같음). 괄호는 리뷰 수정 판의 값입니다(JSON `migration.before_owner_decisions`).

| 값 | S | M |
|---|---|---|
| 전체(두 번의 최솟값) | 1.12초(1.01) | 10.77초(9.05) |
| 복사 / 원본 재확인 / 검증 | 1.00 / 0.03 / 0.02초(0.93 / 0.02 / 0.01) | 9.22 / 0.66 / 0.55초(8.48 / 0.28 / 0.17) |
| 원본 잠금을 쥔 시간 | 1.05초(0.96) | 10.08초(8.81) |
| 최대 RSS(`/usr/bin/time -l`) | 32 MB(31) | 78 MB(77) |
| 게시(2단계 시험의 `probed.publish`) | `publish_new` | `publish_new` |
| 원본 digest(독립 걷기, 잠금 파일 둘 제외) | 전후 같음 | 전후 같음 |
| 두 전환의 사본 | 같음 | 같음 |
| 다시 부르기 | `already_migrated`, `same` 228 | `already_migrated`, `same` 2,036 |
| 보고서의 작성 출처 | `human`·`inferred`·`cli` | 같음 |
| 응답의 작성 출처(리뷰 뒤, OQ14) | `human`·`inferred`·`cli`, 세션 없음, `request_id` 있음 | 같음 |
| 기록 비교(T11) | 참조 477·run 30·기록 상태 252 등 모든 판정이 같음 | 참조 15,000·run 300·기록 상태 9,633 등 모든 판정이 같음 |
| 목적지 `status` | `analytics.project/2`, 쓸 수 있음, 매핑 적용 | 같음 |
| 원본 `status` | `lab.project/1`, `migration_required`, `migration_hint`, 원본 그대로 | 같음 |

이 판의 시간이 긴 것은 기기가 다른 일로 바빴기 때문이고(1분 부하 평균 약 50, 18코어) 코드 때문이 아닙니다: 같은 부하에서 M 전환을 `8d3695f`의
kit과 이 트리의 kit으로 번갈아 두 번씩 부른 시간이 11.15·10.77초와 10.99·11.49초였습니다(`migration.timing_under_load`). 소유자 결정의 변경이 전환에
더한 것은 항목마다의 이름 판정 하나입니다. PR ②의 측정(S 1.01초·29 MB, M 9.40초·73 MB)과 리뷰 전 판(S 1.02초·31 MB, M 9.17초·76 MB)도 같은
크기입니다. 중단 행렬(`fault_matrix`)은
F1–F16과 F15(링크 없이 사본의 직접 쓰기) 37행이 PR ②의 관측과 같고(F15의 정체성 전 끊김은 `destination_mismatch(occupied)`), 이 PR이 더한 행 넷 —
링크가 없는 목적지에서 표식을 `rename_new`로 게시하는 중에 끊김(`marker_begin`·`marker_temp`·`marker_written`, 게시 뒤) — 은 앞 셋이 이어서
커밋하고 마지막이 `already_migrated`입니다(41행 모두 리뷰 전 판과 같고, 소유자 결정 뒤 다시 잰 41행도 전환 id·임시 경로를 빼면 같음). 잠금 뒤 1단계 재판정의 거절은 이제 "nothing was written" 대신 이 호출이
남긴 것(목적지·`.analytics/`·잠금 파일)과 할 일을 적습니다(주입 시험 두 사례, `refused_under_the_lock`). 동시성(T5·T7·T15)과 T13·T10의 writer
시험, T11의 단위 시험은 이 트리에서 통과했습니다(시험 이름은 JSON).

**AppleDouble `._<이름>`**(소유자 결정 2, `migration.appledouble`). 원본의 AppleDouble 파일(루트, 폴더 옆, `data/`, `.lab/` 안)은 복사하지 않고
보고서 `excluded`(`os_metadata`)에 적으며, 복사 중 원본에 새로 생겨도 `source_changed`가 아닙니다. `.DS_Store`와 AppleDouble만 있는 목적지는
빈 것이고(init도 같음, `Thumbs.db`는 여전히 항목), 잠금 뒤 재판정에서 이 전환의 `.analytics`·잠금 옆에 생긴 것과 사본 옆에 생긴 것을 항목으로
세지 않으며, 끊긴 전환을 이을 때 계획에 없는 AppleDouble(루트, 폴더 옆, 사본 옆, `.analytics/` 안)은 무시하고 남겨 둡니다. 시험 여섯 모두
`8d3695f`의 코드에서는 실패합니다.

**보정 뒤 AppleDouble**(`migration.appledouble`, 그 전 판은 `appledouble_before_corrected_decisions`). AppleDouble은 `._<이름>`인 일반 파일
(lstat)만입니다. 원본의 폴더 `._data/`와 링크 `._link`는 복사되고 `excluded`에 없으며, 목적지의 폴더·링크 `._d`는 1단계와 잠금 뒤 재판정에서
`not_empty`, 끊긴 전환을 이을 때 계획에 없는 폴더·링크 `._x`는 `destination_mismatch(unaccounted)`(아무것도 지우지 않음)입니다. 첫 ledger 줄이
끊긴 전환 디렉터리 옆의 `._progress.jsonl`은 내용이 아니어서, 다시 부르면 그 디렉터리를 지우고 새로 시작합니다. macOS의 `rmdir`은 디렉터리에
남은 AppleDouble 일반 파일을 함께 지우고(그것만 남았을 때; 폴더 `._x`나 `._._x`, 다른 파일이 있으면 ENOTEMPTY — scratchpad에서 확인), 다른
시스템에서 남으면 `destination_write_failed`와 그 폴더를 지우라는 hint입니다(시험이 그 경로를 어느 시스템에서나 강제). 새 시험은 모두 `d0c766b`의
코드에서 실패합니다. 전환 S·M은 다시 재지 않았습니다: 걷기에서 바뀐 것은 이름이 `._`로 시작하는 항목의 lstat 판정 하나이고 두 fixture에 그런
이름이 없습니다.

중단 행렬을 이 판의 코드로 다시 관찰했습니다(`migration.fault_matrix_after_corrected_decisions`). 그 전 41행은 전환 id·임시 경로를
빼면 모두 같고, AppleDouble 규칙이 닿는 행 여덟을 더했습니다. F1 셋(잠금 뒤, 전환 디렉터리를 만든 뒤, 첫 줄을
끊음)·F2·F6·F8에서 끊은 뒤 macOS가 FAT·exFAT에 만드는 것처럼 AppleDouble 일반 파일(`._progress.jsonl`, `._.analytics`,
`.analytics/._migration.lock`, `._data`, `data/._raw` 가운데 폴더가 있는 것)을 더하면 여섯 행 모두 이어서 커밋했고, 사본은 중단 없는 전환과
같으며 더한 파일은 남았습니다. 첫 줄을 끊은 F1과 F6에 폴더 `._x`를 더하면 각각 `destination_invalid(not_empty)`와
`destination_mismatch(unaccounted)`였습니다(원본 그대로). 점검 뒤 이 판의 코드로 다시 관찰한 중단 행렬
(`migration.fault_matrix_after_check_of_corrected_decisions`)은 41행과 AppleDouble 여덟 행이 모두 같습니다(`earlier_rows_same`·
`appledouble_rows_same`). AppleDouble 규칙 자체는 이 판에서 목적지의 판정(복사가 원본의 항목을 놓을 경로의 `._` 파일)과 거절 hint만 바뀌었고,
목적지 충돌 시험 셋(폴더·링크·`.lab/._d`)과 지우지 못한 디렉터리 시험을 더했습니다(`migration.appledouble.in_the_way`).

**fixture의 재현성**(`migration.fixture_reproducibility`). 리뷰 전 판은 S·M의 바이트와 digest가 PR ②의 fixture와 다른 까닭을 "도구가 PR ②
뒤에 바뀌어서"라고 적었는데, 근거 없는 문장이었습니다. 재어 보니: fixture의 바이트는 재현되지 않습니다. 이 브랜치의 생성기로 같은 `--out`에 두 번
만든 S는 `run.json`의 `duration_s`와 `environment.json`의 시간 값이 다르고, 가져오기 기록의 `source.path`와 traceback은 절대 경로(`--out`, kit)를
담습니다. 이 기록의 전환 행이 쓰는 원본 digest(`tree_digest`)는 바이트의 것이라 어느 두 생성 사이에서도 다릅니다. 생성기 자신의 digest(시간 값을
가림)는 두 생성이 같고(`sha256:962977…`, 리뷰 전 판의 S도 다른 `--out`에서 같음), PR ②의 생성기(`ebd10b6`의 `tools/eval_fixtures.py`와 그
`src`)로 만든 S의 digest(`sha256:f27ec3…`)와는 traceback 하나(`k03-join-profiles/stderr.txt`)에 든 kit 경로만 다릅니다: 그 경로를 이 생성의
`--kit-root` 경로로 바꾸면 PR ②의 S가 `sha256:962977…`이 됩니다. 그래서 이 PR의 구현 체크리스트(run 폴더) G3-08(a)는 바이트가 아니라 생성기의 digest로 읽습니다. JSON의
fixture 필드는 `kit_commit`(fixture를 쓴 kit, `V1_KIT_COMMIT`)과 `generator`(생성기)로 나눴습니다.

## uv(`uv`, T18)

`tests/test_migrate_command.py`가 실제 uv로 남긴 관찰입니다(소유자 결정 뒤 다시 남긴 열두 관찰이 그 전 판과 같음). 두 런처 모두 맞는 lock과 맞지 않는 lock에서 0으로 끝나고 원본의 `.venv/`·`uv.lock`과
목적지의 lock을 바꾸지 않습니다. 맞지 않는 lock은 sync가 실패하고 stderr가 `the migration is done, but preparing the environment of <dest>
failed (uv sync --locked, exit 1); the project's next command prepares it`입니다.

리뷰 뒤 더한 사례(`uv-tilde-*`): `--to=~/x`와 `--to '~/x'`(셸이 `~`를 풀지 않은 그대로)를 두 런처로, `.venv/`가 없는 원본과 lock에 없는 패키지가
`.venv/`에 있는 원본에서 불렀습니다(HOME은 시험의 디렉터리). 여덟 사례 모두 0이고, 목적지는 kit이 HOME 아래에 만든 `x`이며 그 `.venv/`를 만들고,
원본의 `.venv/`·`uv.lock`은 바이트 그대로이고 원본 안에 `~` 폴더가 없습니다. 고치기 전의 런처에서는 여덟 사례 모두 실패했습니다: 설치본은
`uv sync --project <원본>/~/x`가 원본의 pyproject를 찾아 원본에 `.venv/`를 만들고(추가 패키지는 지움) 성공이라 적었고, `bin/analytics`는
`cd -- "~/x"`에서 1로 끝났습니다(run 폴더 `fix/evidence/tilde-on-old-launchers-detail.txt`).

## aelix가 부른 CLI의 작성 주체(`agent_cli`, T22)

aelix 0.1.0b2가 자기 로더로 확장을 로드하고 harness를 만든 뒤(`on("user_bash")`는 harness의 hook 버스로 연결, 세션은 시험 디렉터리의 JSONL), kit
CLI를 aelix의 bash 도구와 `cli/repl.handle_user_bash`로 불렀습니다. CPython 3.12와 3.11의 결과가 같습니다. 소유자 결정 뒤 다시 남긴 관찰도 그 전
판과 같습니다(kit 출력의 Workspace 빌드 시각만 다름). aelix 0.1.0b2의 `hooks._reducer_user_bash`가 결과의 종류와 상관없이 마지막
`UserBashResult`를 쓰는 것을 이 판에서 다시 읽었습니다. 보정 뒤(2026-10-09, [#13](https://github.com/handochan/aelix-analytics/issues/13#issuecomment-6072183477)) 다시 남긴 관찰에서 그 전 판의 경우는
같고(빌드 시각만 다름, `agent_cli.earlier_cases_same_as_before`), 다른 명령 뒤에 kit을 부르는 `!` 줄
(`echo T22-before; <kit record>`)의 경우를 더했습니다(아래 "보정 뒤" 행). 그 전 판의 관찰은 `agent_cli.before_corrected_decisions`에 있습니다.
점검 뒤(코드 커밋 `fix: answer the check of the corrected decisions`) 다시 남긴 관찰도 보정 뒤와 같고(빌드 시각만 다름,
`agent_cli.earlier_cases_same_after_the_check`), 점검이 찾은 줄 — 막는 확장이 먼저일 때 `<kit record note> $'Q3\'s numbers' ; echo
T22-SECOND-RAN  # it's fine`(bash·zsh가 `$'…'`를 escape된 따옴표로 읽어 `;`가 진짜 둘째 명령) — 이 두 순서 모두 막힌 채이고 아무것도 쓰이지 않음을
더했습니다(그 전 코드는 이 줄에 셸을 돌려줘 둘째 명령이 막음을 지나 돌았음). 그 전 판의 관찰은 `agent_cli.after_corrected_decisions`에 있습니다.
판정을 이 기기에서 찾은 셸(sh·bash·zsh·dash·ksh·pwsh)로 실제로 돌려 본 함정 시험은 `user_bash_shell_traps`에 있습니다(kit 명령 하나로 판정한 줄이
그 셸에서 둘째 명령을 돌리지 않음).
`221dfbb` 점검 뒤(판정만 좁힘) 다시 남긴 T22 관찰은 `221dfbb`의 것과 같아(빌드 시각·임시 경로만 다름, `agent_cli.same_after_the_221dfbb_checks`)
위 표와 JSON의 값을 그대로 두었습니다. T22의 줄은 이 인터프리터의 `python.exe`를 경로로 부르고 cmd가 읽는 글자가 없어 Windows에서도 판정이 같습니다.
함정 시험에는 줄 `analytics 'x&@SENT@'`를 더했습니다(배치 파일이 받을 때만 둘째 명령이 되는 줄; 이 기기의 셸에서는 한 단어라 받고 둘째 명령이
돌지 않음). 맨 이름이 배치 파일로 풀려 cmd가 인자를 다시 읽는 경우는 Windows 기기가 없어 실행하지 않았습니다(`agent_cli.windows_batch_file`).
판정 비교(`judgment_after_the_221dfbb_checks`, 줄을 돌리지 않음)는 `221dfbb`의 판정과 이 판의 판정을 표의 행·함정 줄·무작위 줄에 다섯 셸 이름과
Windows 여부로 대어, 새로 받는 줄이 없고 Windows 밖에서 새로 받지 않는 줄은 PowerShell의 프로그램 단어 행뿐임을 보입니다.

| 경우 | 결과 |
|---|---|
| 에이전트의 bash 도구 | 기록 `agent`·`declared`·`cli`, 그 세션 |
| 사람의 `!` 명령(kit 호출), 다른 확장 없음 | 기록 `human`·`declared`·`cli`, 그 세션 |
| 사람의 `!echo T22`(kit 호출 아님) | 이 확장은 셸을 돌려주지 않음, aelix의 셸이 돎 |
| 셸을 돌려주는 다른 확장이 먼저 등록 | kit 명령은 이 확장의 셸(`PersonShell`)로 돌아 `human`, 다른 확장의 셸은 그 명령에서 돌지 않음. `echo`는 다른 확장의 셸 |
| 셸을 돌려주는 다른 확장이 나중에 등록 | kit 명령도 다른 확장의 셸로 돌아 `agent`(현재 계약 A의 한계), `echo`는 다른 확장의 셸 |
| 모든 `!` 명령을 막는 확장이 먼저 등록(리뷰 뒤) | kit 명령 하나인 줄은 이 확장의 셸로 돌아 기록이 쓰이고 `human`(막음을 대신함: 현재 계약 A의 한계, 소유자가 2026-10-09에 그 범위를 kit 명령 하나로 정함), `echo`는 막힘 |
| 막는 확장이 나중에 등록(리뷰 뒤) | kit 명령도 `echo`도 막힘, 기록 없음 |
| 다른 명령과 합친 줄(`echo T22-before; <kit record>`), 다른 확장 없음(보정 뒤) | 이 확장은 결과를 돌려주지 않음, aelix의 셸이 돌아 기록 `agent`·`declared`·`cli`, 그 세션 |
| 합친 줄, 셸을 돌려주는 다른 확장이 먼저·나중(보정 뒤) | 두 순서 모두 다른 확장의 셸이 돌아 `agent` |
| 합친 줄, 막는 확장이 먼저·나중(보정 뒤) | 두 순서 모두 막힘, 기록 없음(그 전 코드는 막는 확장이 먼저면 이 줄도 이 확장의 셸로 돌렸음) |
| `$'…'`가 든 줄, 막는 확장이 먼저·나중(점검 뒤) | 두 순서 모두 막힘, 기록 없음(그 전 코드는 막는 확장이 먼저면 둘째 명령이 막음을 지나 돌았음) |
| 확장 자신의 kit 자식(`/analytics`) | 기록이 아니라 그 자식의 환경: `AELIX_ANALYTICS_ACTOR=human`, `/analytics`면 `AELIX_ANALYTICS_MEANS=command`(프로세스 환경의 `agent`와 무관) |

Windows의 PowerShell·cmd 자식은 실행하지 않았습니다(CI의 Windows aelix 레그가 돌립니다. 함정 시험도 Windows에서는 건너뜁니다).

## 링크가 없는 파일 시스템(`foreign_fs`)

**소유자 결정 뒤(`foreign_fs.shipped_after_owner_decisions`).** macOS 26.6.2에서 이 판이 새로 만든 FAT32·exFAT 디스크 이미지(`hdiutil create -fs
"MS-DOS FAT32"`·`-fs ExFAT`, `attach -nobrowse`, fskit)에 opt-in 시험(`AELIX_ANALYTICS_TEST_FOREIGN_FS`)을 출하 코드로 돌렸습니다. FAT32: `link`는
없고 `rename_new`가 있어 전환이 `rename_new`로 표식을 게시해 커밋했고, 다시 부르면 `already_migrated`(다른 사본 없음)였습니다. 목적지에는 macOS가
만든 AppleDouble이 사본·관리 파일마다 있었습니다(`._.analytics`, `._journal.jsonl`, `.analytics/._migration.lock` 등). 커밋한 목적지에서 kit CLI의
`status`·`record`·`run`이 모두 0이었고 run 목록은 실제 run 하나였습니다(AppleDouble을 run으로 보지 않음). exFAT: `rename_new`도
`RenameUnsupported`(errno 45)라 2단계 시험에서 복사 전에 `destination_invalid(no_exclusive_rename)`이었습니다(소유자 결정 1). `8d3695f`의 코드는
두 볼륨 모두 1단계 `destination_invalid(not_empty)`(`._.analytics`, `.analytics/._migration.lock`)로 멈춥니다(강화한 opt-in 시험이 거기서 실패).

**보정 뒤(`foreign_fs.after_corrected_decisions`).** 같은 디스크 이미지에서 opt-in 시험 둘 — 그 전의 전환 시험과 보정한 AppleDouble 규칙의
시험 — 이 이 판의 코드로 통과했습니다(4 passed, 51 deselected). 전환 시험의 결과는 그 전 판과 같습니다(FAT32 커밋·다시 부르면
`already_migrated`, exFAT `no_exclusive_rename`). 새 시험: 일반 파일 `._x`만 있는 폴더에 init이 프로젝트를 만들었고, 폴더 `._d`가 있는 폴더는
init이 `init_not_empty`, 전환이 1단계 `not_empty`로 거절했습니다. 첫 ledger 줄을 끊어 둔 전환 디렉터리에는 두 볼륨 모두 macOS가
`._progress.jsonl`을 스스로 썼고, 다시 부른 전환이 그 디렉터리를 지우고 새로 시작해 FAT32는 커밋, exFAT은 2단계 `no_exclusive_rename`이었습니다.
`d0c766b`의 코드에서 새 시험은 두 볼륨 모두 init이 폴더 `._d` 옆에 프로젝트를 만들어 실패합니다.

**점검 뒤(`foreign_fs.after_check_of_corrected_decisions`).** 같은 디스크 이미지에서 같은 opt-in 시험 둘을 이 판의 코드로 다시 돌렸고(4 passed),
관찰이 보정 뒤와 같습니다(전환 id만 다름, `same_as_before`). 이 판의 AppleDouble 변경(목적지 충돌의 미리 거절)은 이 시험의 목적지에는 닿지
않습니다(원본에 `._` 폴더·링크가 없음).

**그 전 판(G3, 키 `mnt-fat`·`mnt-exfat`).** 명령 그룹(G3)이 macOS 26.6.2의 FAT32·exFAT 디스크 이미지(fskit, nobrowse)에서 잰 것이며, 리뷰 전 판의
값을 그대로 옮겼습니다. 두 볼륨 모두 `link`가 ENOTSUP이고, `renamex_np(RENAME_EXCL)`은 있는 이름에서 EEXIST(바이트 그대로)이며 없는 이름은
FAT32가 rename, exFAT이 ENOTSUP입니다. 그때 출하된 전환은 두 볼륨 모두 1단계 `destination_invalid(not_empty)`로 멈췄습니다: macOS가 확장 속성이
없는 볼륨에서 파일마다 AppleDouble `._<이름>`을 만들기 때문입니다. AppleDouble을 지나치게 한 실험 실행은 FAT32에서 `rename_new`로 표식을 게시해
커밋했고(사본 다섯은 직접 쓰기, 표식의 직접 쓰기 없음), exFAT에서는 2단계 `no_exclusive_rename`으로 멈췄습니다. 이 결과로 소유자가 AppleDouble을
운영체제 메타 파일로 정했습니다([#13](https://github.com/handochan/aelix-analytics/issues/13#issuecomment-6070315072)).

## 측정 메모

이 판의 첫 측정(2026-10-08T20:06:50Z)은 고친 뒤의 kit 바이트코드가 낡은 채였습니다(run 환경의 `PYTHONDONTWRITEBYTECODE=1`). 그래서 자식마다 kit을
컴파일하며 시작해 최대 RSS가 S 44 MB·M 86 MB로 컸습니다(나머지 값은 둘째 측정과 잡음 안). kit의 바이트코드를 만들고(`python -m compileall
src/aelix_analytics`; `__pycache__`는 추적하지 않음) 측정 전체를 다시 돌렸고, 이 기록의 값은 둘째 측정의 것입니다. 첫 측정의 파일은 run 폴더
(`fix/measure-stale-pyc`)에 있습니다. 리뷰 전 판의 측정은 최신 바이트코드였습니다.

소유자 결정 뒤의 측정(2026-10-08T23:26:30Z, run 폴더 `ci-fix/measure`)은 측정 전에 kit의 바이트코드를 만들었고, HOME을 run 폴더의 가짜 디렉터리로
두었습니다. 기기가 다른 일로 바빠(1분 부하 평균 약 50) 시간 값이 그 전 판보다 길고, 같은 부하에서 두 코드를 번갈아 잰 값은 같은 크기입니다(위).

보정 뒤의 판(2026-10-09, run 폴더 `corrected/`)은 같은 격리로 코드가 닿는 부분만 다시 쟀습니다(`measured_tree.after_corrected_decisions`). 중단
행렬은 그 전 판처럼 시간 값이 없는 관찰입니다.

점검 뒤의 판(2026-10-09, run 폴더 `round5/`)도 같은 격리로 코드가 닿는 부분만 다시 쟀습니다
(`measured_tree.after_check_of_corrected_decisions`). 판정을 실제 셸로 돌려 본 탐색(무작위 줄 포함)은 scratchpad에서 했고, 저장소에는 그 결과를
시험(`user_bash_shell_traps`)으로만 남겼습니다.

`221dfbb` 점검 뒤의 판(2026-10-09, run 폴더 `round6/`)도 같은 격리로 판정이 닿는 부분만 다시 쟀습니다
(`measured_tree.after_the_221dfbb_checks`). 기기가 다른 일로 바빠(1분 부하 평균 약 47) 시간 값은 그 전 판보다 깁니다.

## 실행하지 않은 것

- 이 판의 커밋(`221dfbb` 점검 처리)의 저장소 CI(Linux·Windows·macOS 레그, `pending`: 아직 push하지 않음; `221dfbb`의 CI는 이 커밋을 만들 때 돌고 있었음). Linux의 `renameat2`(ctypes), Windows의
  `rename_new` = `publish_new`, 런처의 `--no-project`와 응답 읽기를 Windows에서, Windows 전용 시험, T22의 PowerShell·cmd 자식과 `$'…'`를 포함한 줄,
  셸 군마다의 `!` 줄 표와 함정 시험(Windows는 건너뜀), AppleDouble 규칙(Windows의 junction `._x`), 고친 긴 필드 시험의 Windows 실행이 그 CI에 걸려 있습니다. `8d3695f`의 Windows 저장소 시험 단계는 35분 한도 가운데 1,789초를 썼습니다.
- 실제 이동식 FAT·exFAT 매체와 Linux·Windows의 FAT·exFAT(macOS 디스크 이미지만).
- Windows에서 맨 `analytics`·`python`·`py`·`uv`가 배치 파일(버전 관리자의 shim)로 풀려 cmd가 인자를 다시 읽는 경우(`221dfbb` 점검의 꼭 고칠
  것; 그런 줄을 받지 않게 고쳤지만 Windows 기기가 없어 실행하지 않았고, 배치 파일을 띄우는 시험은 없습니다).
- 전원 손실과 그 뒤의 내구성, Windows의 디렉터리 flush·읽기 전용 사본·잠금 해제 지연, 네트워크 파일 시스템.
- 실제 aelix 모델 세션(bash 도구와 `handle_user_bash`를 직접 부름), aelix 밖의 에이전트 호스트, aelix RPC의 `bash`.
- 색인이 있는 writer의 비용(#15).
- 확장의 `analytics_import`·`analytics_run`에 Windows 명령줄(32,767자)을 넘는 설명·제목(그 전부터 그곳에서는 `spawn_failed`, 이 PR의 변경 아님).
