# Issue #13 복사 전환 엔진 측정(PR ②) — 2026-10-08

[#13](https://github.com/handochan/aelix-analytics/issues/13)의 둘째 구현 PR ②(kit 0.7.0, 번호 그대로)는 `lab.json` 프로젝트를 새 폴더에 새 형식으로
복사하는 전환 엔진(`aelix_analytics.migration`), 포트 메서드 넷(`FileLocks.try_exclusive`, `AtomicFiles.publish_new`·`flush_handle`·`flush_dir`),
전환 기록 `analytics.migration/1`과 표식의 `migration`, 커밋 전 목적지의 판정 `incomplete`(`migration_incomplete`), 전환한 프로젝트의 매핑 읽기를
더합니다. 기존 프로젝트를 변환하는 일이므로 운영 정책의 측정 기록이며, [설계](../design/RECORD-WRITER-AND-MIGRATION.md) 12절이 이 기록에 더한 키
`fixtures`·`phases`·`sizes`·`preservation`·`fault_matrix`·`concurrency`·`reader_comparison`이 있습니다. 개발 기기(macOS)의 값이고, Linux·Windows의
CI 칸은 PR을 연 뒤의 실행으로 채웁니다(지금 `pending`). 모든 값과 JUnit 파일의 sha256은 [2026-10-08-issue-13-migration.json](2026-10-08-issue-13-migration.json)에
있습니다. 계약에 반영한 것은 [CONTRACTS.md](../CONTRACTS.md) B("표식 판정과 쓰기", "프로젝트 찾기", "프로젝트 전환")·D·E·F·F1(kit 0.7.0)·G입니다.

전환은 아직 명령으로 열지 않았습니다(`analytics migrate`는 셋째 PR). 그래서 아래의 전환은 모두 시험과 이 측정의 스크립트가 엔진을 직접 부른
것입니다.

이 기록은 PR의 리뷰(독립 구현 리뷰 1회, Codex 리뷰 1회)의 지적을 고친 커밋(`fix: answer the reviews of PR 2 (Issue #13)`)을 처음부터 다시 잰
것입니다. 고친 것이 `migration.py`·`core.py`·`relations.py`라 모든 값을 다시 쟀고, 처음 측정(코드 `2b6eabb`)과의 비교는 끝의 "리뷰 뒤 다시 잼"과
JSON의 `previous_measurement`에 있습니다.

## 출처와 환경

| 항목 | 근거 |
|---|---|
| 측정한 코드 | `feat/issue-13-migration-engine`의 커밋 `fix: answer the reviews of PR 2 (Issue #13)`의 코드. `origin/main` `ed29cf0`(PR #80 병합, 트리는 `fbd12e1`과 같음) 위의 `b7e1521`·`8ca6914`·`50a55bb`·`d08cf24`·`4243021`·`f7faea5`와 그 커밋입니다. 그 커밋을 run 폴더의 깨끗한 클론에서 쟀고, 이 기록과 PR 글은 잰 뒤 같은 커밋에 더했습니다(커밋을 고쳐 써 잴 때의 id `50bbbdb`는 branch에 남지 않음). JSON의 `measured_tree`는 파일 manifest의 형식(추적하는 `src`·`extension`·`tests`·`tools`·`bin`·`pyproject.toml`·`uv.lock` 파일마다 `shasum -a 256` 한 줄, 경로 순), 그 manifest 전체의 sha256과 파일 수를 담습니다. 파일마다의 목록은 run 폴더의 `evidence/code-manifest.txt`에 있고 저장소에는 없습니다 |
| 비교한 기준 | `fbd12e1`(= `c918381`의 코드, 이 PR이 올라가는 PR #80의 헤드)의 체크아웃에서 처음 측정의 새 시험을 돌렸고, 리뷰 뒤 더한 시험은 앞 헤드 `f7faea5`의 체크아웃에서 돌렸습니다(아래) |
| 워크플로 | `.github/workflows/ci.yml`, 이 변경이 고치지 않음 |
| CI | `pending`: PR을 열지 않았습니다(이 작업은 push하지 않음). PR의 실행으로 `runs`·`legs`를 채웁니다 |
| 개발 기기 | macOS 26.6.2 arm64(Apple M5 Pro), uv 0.11.19. 저장소 시험 CPython 3.12.13(`uv.lock`의 개발 환경), aelix 레그 3.12·3.11, 런처·stdlib 스모크 3.11. 같은 기기에서 다른 에이전트의 시험이 함께 돌았으므로 시간은 그 부하를 담습니다 |
| 격리 | `AELIX_ANALYTICS_TRUST_FILE`·`AELIX_ANALYTICS_UV`를 run 폴더 안으로(`uv`는 실제 uv로의 링크), `LAB_*`·`AELIX_LAB_*` 없음, `~/.aelix`는 읽지도 쓰지도 않음 |

## 결과

| 검사 | 명령 | 결과 |
|---|---|---|
| ruff | `uv run --locked ruff check tests extension src tools` | 통과 |
| 저장소 시험 | `uv run --locked pytest -q -ra -p no:cacheprovider --timeout 600 --timeout-method=thread` | 통과: 1644 passed, 92 skipped, 0 failed(460초). 처음 측정(1621 passed)보다 리뷰에 더한 시험 23개가 많고, PR ①의 마지막 측정(1477 passed, 72 skipped)보다 167개 많음: 전환 시험 `tests/test_migration.py`·`tests/test_migration_reader.py`, 포트 계약 사례, 표식 판정의 `incomplete` 행, Windows의 `publish_new`, aelix 프로세스가 `migration`을 로드하지 않음. 늘어난 건너뜀 20개는 Windows 구현의 포트 사례 14, 시험 adapter가 밝히지 않은 능력 3, 다른 OS에서 도는 전환 사례 3(공유 없이 연 원본 파일과 MAX_PATH는 Windows, UTF-8이 아닌 이름은 Linux)입니다. 리뷰에 더한 시험 가운데 읽기 전용 폴더와 링크 뒤 `..`의 둘은 POSIX에서만 돌고 이 기기에서 돌았습니다 |
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
| 리뷰의 새 시험을 앞 헤드에서 | 이 커밋의 `tests/test_migration.py`·`tests/test_migration_reader.py` + 앞 헤드 `f7faea5`의 코드(run 폴더의 클론) | 기대대로 실패: 리뷰에 더한 23개 모두(지적마다의 재현). 그 헤드에 있던 110개는 통과, 2개 건너뜀 |

## 중단과 이어 쓰기(`fault_matrix`, T6)

[설계](../design/RECORD-WRITER-AND-MIGRATION.md) 6.11절의 F1–F16을 `tests/test_migration.py`의 `FAULTS`가 한 행씩 돌립니다. 풍부한 원본(LF·CRLF 줄,
`/1` writer가 끊긴 줄에 붙여 쓴 줄, `/2` 줄, 모르는 가족 줄, 끊긴 마지막 줄, 한국어·NFD 이름, 빈 파일, 2 MiB 넘는 파일, 읽기 전용 파일, `.lab/`의 이력·로그·
캐시·잠금, 복사하지 않는 `.venv/`·`report/`·`__pycache__/`·`.DS_Store`, 옛 SDK 이름의 스크립트, 밖을 가리키는 폴더·파일 링크, 다시 만드는 안쪽 링크, 깨진
안쪽 링크, 루트와 바깥 폴더를 절대 경로로 기록한 옛 kit의 run)을 자식 프로세스에서 전환하다 그 단계에서 `os._exit(9)`로 끝내고
(`tests/migration_driver.py`), 남은 목적지를 판정한 뒤 같은 전환을 이 프로세스에서 다시 부릅니다. 기대: 표식 전에는 `incomplete`이고 kit이 읽기까지
`migration_incomplete`로 거절하며 옛 kit의 찾기(`lab.json`만)는 아무것도 찾지 않음, 다시 부르면 중단 없는 전환과 같은 사본(전환 디렉터리·표식·
`.gitignore` 머리 줄의 전환 id 제외), 원본 트리(독립 걷기, 잠금 파일 둘 제외) 그대로, 표식이 보고서를, 보고서가 매핑과 모든 사본을 묶음, 임시 파일
없음. 아래 "남은 것"·"다시 부른 결과"는 같은 행들을 run 폴더의 `fault_observe.py`가 헤드에서 다시 돌려 적은 것이고, 시험 결과는 개발 기기에서 모두
통과입니다.

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

`tools/eval_fixtures.py scale --scale S|M --seed 0`을 잰 코드(kit 0.7.0이 `lab.json`·`lab.*/1`을 씀)로 새로 만들고, 각 원본을 새 목적지에 두 번 전환했습니다
(자식 프로세스마다 하나, 시간은 ledger 줄의 시각에서, 두 번의 최솟값). 원본은 측정 스크립트의 걷기(전환 코드가 아닌 것)로 첫 전환 전과 둘째 전환·다시
부르기 뒤에 비교했습니다.

| 항목 | S | M |
|---|---|---|
| 원본 파일·바이트(잠금 파일 둘 제외) | 229개, 2,898,447 B | 2,037개, 223,109,249 B |
| 복사한 파일·바이트(`lab.json`의 바이트는 `source-marker.json`으로, `.lab/report.lock`은 뺌) | 228개, 2,898,447 B | 2,036개, 223,109,249 B |
| 복사하지 않은 것 | `.lab`의 잠금 셋, 빈 `.lab/cache`·`report`(0 B) | 같음 |
| 계획 / 복사 / 원본 재확인 / 사본 검증(ms) | 9 / 930 / 21 / 11 | 48 / 8,442 / 267 / 176 |
| `.gitignore` 변환 / 보고서 / flush / 표식 / 전체(ms) | 9 / 20 / 2 / 4 / 1,011 | 21 / 27 / 17 / 8 / 9,030 |
| 원본 잠금을 쥔 시간(ms) | 962 | 8,763 |
| 자식의 최대 RSS(시작 때) | 30.3 MB(17.2 MB) | 74.1 MB(17.3 MB) |
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
| M | 참조 15,000, run 300, 기록 상태 9,633, 계보 483, 생산자 483, 가져오기 20, 끊긴 줄 4 | 모두 같음, 매핑 적용, 읽기 0.32초 |

`tests/test_migration_reader.py`가 옛 kit의 경로를 봅니다(심볼릭 링크, Windows 레그는 junction도): 옛 run이 루트를 다른 철자로 기록한 절대 경로,
`data -> /ext/data` 경계를 지나 실제 경로로 기록한 입력, 상대 `.lab/…` 경로, 같은 글자를 기록한 0.5.0 run. 목적지의 판정이 원본과 같고(다른 것은 `.lab/…`
경로에 매핑만이 주는 `read_as: .analytics/…` 하나), 목적지를 옮기고 `/ext/data`를 지운 뒤에도 같으며, 보고서의 바이트가 바뀌면 매핑을 쓰지 않고
`status`가 `report_changed`를 보입니다. 리뷰 뒤에는 매핑을 지우면 `source_revision`이 바뀌고 같은 바이트로 되돌리면 처음 값으로 돌아오는 것,
표식·보고서·매핑의 전환 id가 다르거나 보고서가 다른 전환의 디렉터리에 있으면 매핑을 쓰지 않는 것(`invalid`)도 봅니다.

## 실행하지 않은 것

JSON의 `not_run`에 있습니다: 저장소 CI(Linux·Windows, macOS는 라벨 — Windows 레그가 F 표, 다른 프로세스가 공유 없이 연 원본 파일, LongPathsEnabled 1·0의
260자 넘는 목적지, junction, Windows의 포트 구현을 돌림), 표식 뒤의 전원 손실과 Windows의 디렉터리 flush, FAT·exFAT 목적지(링크를 거절하는 포트로만
흉내 냄), 네트워크 파일 시스템·Linux casefold 디렉터리·Windows의 디렉터리별 대소문자 구별 상속, Linux에서 만든 예약 이름 원본을 Windows로 전환,
다른 드라이브·볼륨의 목적지, 명령 `analytics migrate`와 런처(셋째 PR)·실제 aelix 세션, 읽기 전용으로 마운트한 볼륨(읽기 전용 폴더 트리로 흉내 냄),
M의 Workspace 페이지 비교(판정만 비교함). 링크 없는 목적지의 표식 직접 쓰기가 끊긴 경우(F15 행의 표식)는 실제 매체가 아니라 링크를 거절하는
포트로만 보았고, 그 결과(`marker_invalid`)를 바꾸는 일은 소유자 질문입니다(권장: 있는 곳에서는 없을 때만 이름을 주는 rename — Linux
`renameat2(RENAME_NOREPLACE)`, macOS `renamex_np(RENAME_EXCL)` — 으로 게시하고 없으면 표식 전에 거절).

## 리뷰 뒤 다시 잼

처음 측정(코드 `2b6eabb`, 이 PR의 커밋 `f7faea5`가 담은 기록)과 비교하면, F 표의 37행은 남은 것·kit의 읽기·다시 부른 결과·사본·원본 비교가
모두 같고, 규모의 값은 같은 기기의 부하 안에서 비슷합니다(S 전체 1,025 → 1,011 ms, M 9,315 → 9,030 ms, M의 최대 RSS 69.8 → 74.1 MB). 저장소
시험은 리뷰에 더한 시험만큼 늘었습니다(위 결과 표). 리뷰에 더한 시험 23개는 앞 헤드 `f7faea5`의 코드에서 모두 실패하고(그 헤드의 시험 110개는
통과), 이 커밋에서 모두 통과합니다(JSON의 `new_tests_on_previous_head`).
