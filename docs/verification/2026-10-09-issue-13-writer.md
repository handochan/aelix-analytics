# Issue #13 새 writer·정책 전환·`analytics migrate` 측정(PR ③) — 2026-10-09

[#13](https://github.com/handochan/aelix-analytics/issues/13)의 셋째 구현 PR ③(kit 0.8.0)은 새 형식의 공통 writer(`writer.Writer`)로 모든 쓰기
진입점을 옮기고, 쓰는 형식을 `analytics.project/2`로 바꾸며(`lab.json` 프로젝트는 `migration_required`), 저장 이름을 새 배치로 바꾸고, 전환을
명령 `analytics migrate`로 열고, aelix 안의 작성 주체를 확장이 aelix 프로세스의 환경과 `user_bash` 처리로 밝힙니다. 기록 형식과 프로젝트 전환을
바꾸는 PR이라 운영 정책의 측정 기록이며, [설계](../design/RECORD-WRITER-AND-MIGRATION.md) 12절이 이 기록에 정한 키 `writer_latency`·
`entry_points`·명령 E2E(`command_e2e`)·`migration`(PR ②의 항목 다시 잼)·`uv`(T18)·`agent_cli`(T22)가 있습니다. 모든 값은
[2026-10-09-issue-13-writer.json](2026-10-09-issue-13-writer.json)에 있습니다. 계약에 반영한 것은 [CONTRACTS.md](../CONTRACTS.md) A–G와 F1의
kit 0.8.0 절입니다.

이 기록의 값은 모두 개발 기기(macOS)의 것입니다. 이 브랜치는 아직 push하지 않아 저장소 CI(Linux·Windows·macOS 레그)가 돌지 않았습니다(`runs`·
`legs`가 `not_run`). PR이 열리면 그 실행으로 이 칸을 채웁니다.

## 출처와 환경

| 항목 | 근거 |
|---|---|
| 측정한 코드 | `feat/issue-13-new-writer`의 헤드 `54e9098`(커밋 다섯: writer → 진입점·저장 이름 → 명령 → 확장의 작성 주체 → 문서)의 코드. `origin/main` `0f3f09f`(PR #82 병합) 위입니다. 그 worktree(커밋하지 않은 것 없음)에서 쟀고, 이 기록은 그 뒤의 docs 커밋입니다(코드를 바꾸지 않음). JSON의 `measured_tree`는 파일 manifest(추적하는 `src`·`extension`·`tests`·`tools`·`bin`·`pyproject.toml`·`uv.lock` 파일마다 `shasum -a 256` 한 줄, 경로 순, 219개)의 sha256을 담고, 목록은 run 폴더에 있습니다 |
| 비교한 기준 | writer 지연과 fixture는 PR ③ 전의 kit(`ebd10b6`, `tools/eval_fixtures.py`의 `V1_KIT_COMMIT`; PR #82의 헤드로 main에 있음)의 `src/`를 `git archive`로 꺼내 썼습니다. 이 그룹의 새 시험은 앞 커밋 `d3f0fcf`와 `ebd10b6`의 코드에서 돌렸습니다(아래) |
| 워크플로 | `.github/workflows/ci.yml`: aelix 레그가 `tests/test_agent_cli.py`도 돌리고 관찰을 `evidence-aelix-py3.12/`·`3.11/`에 남깁니다(이 PR의 변경) |
| 코드 실행(CI) | 실행하지 않음(push 전) |
| 개발 기기 | macOS 26.6.2 arm64(Apple M5 Pro), uv 0.11.19. 저장소 시험 CPython 3.12.13(`uv.lock`의 개발 환경), aelix 레그 3.12.13·3.11.15, 런처·stdlib 스모크 3.11 |
| 격리 | `AELIX_ANALYTICS_TRUST_FILE`·`AELIX_ANALYTICS_UV`를 run 폴더 안으로(`uv`는 실제 uv로의 링크), `LAB_*`·`AELIX_LAB_*` 없음. 잰 명령은 `~/.aelix`를 읽거나 쓰지 않습니다. T22는 aelix를 시험의 임시 디렉터리를 HOME·`AELIX_CODING_AGENT_DIR`로 한 채 로드합니다(모델·네트워크·로그인 없음) |

## 결과

| 검사 | 명령 | 결과 |
|---|---|---|
| ruff | `uv run --locked ruff check tests extension src tools` | 통과 |
| 저장소 시험 | `uv run --locked pytest -q -ra -p no:cacheprovider --timeout 600 --timeout-method=thread` | 통과: 1,895 passed, 106 skipped(485초). 건너뜀은 Windows 전용·`AELIX_ANALYTICS_TEST_INSTALL`·Linux 전용·opt-in FAT·exFAT 사례와, aelix가 없는 개발 환경에서 건너뛰는 aelix 시험 파일 셋 |
| 계층·포트 계약 | `tests/test_layers.py tests/test_contracts.py` | 통과: 126 passed, 52 skipped |
| aelix 0.1.0b2, CPython 3.12 / 3.11 | `tests/test_extension.py tests/test_usage_report.py tests/test_agent_cli.py`(CONTRIBUTING의 격리 환경) | 통과: 220 / 220 passed |
| 런처, CPython 3.11 | `tests/test_launcher.py` | 통과: 176 passed, 1 skipped(Windows) |
| 사용량 보고서 | `tools/usage_report.py`(aelix 0.1.0b2, 3.12) | 필수 사실 58/58, 불변식 42/42. 앞 그룹(명령, `d3f0fcf`)의 보고서와 텍스트 76개가 임시 경로와 `source_revision`을 맞추면 같고 숫자 잎의 차이 0: 이 그룹은 모델에 가는 글을 바꾸지 않습니다 |
| 플랫폼 스모크 | `tools/platform_smoke.py --gate required` / `--stdlib-only`(3.11) | holds 25, violated 0 / holds 15, violated 0, not_run 2(이 플랫폼에서 필수 아님) |
| 문서 검사 | `tools/check_docs.py` | `ok: true`, 오류 0(Markdown 65, 로컬 링크 718) |
| `git diff --check origin/main HEAD` | | 출력 없음 |
| 새 시험을 이전 코드에서 | 이 그룹의 새 시험(`tests/test_agent_cli.py`와 `tests/test_extension.py`의 작성 주체 시험, 56개)을 `d3f0fcf`·`ebd10b6`의 코드에 지금의 `tests/`를 넣고 aelix 레그(3.12)로 | 기대대로 두 코드 모두 56개 모두 실패(확장이 환경에 작성 주체를 넣지 않고 `user_bash` 처리기·판정 함수가 없음) |

앞 그룹(G1 writer, G2 진입점·저장 이름, G3 명령)의 새 시험을 그 앞 코드에서 돌린 결과는 각 그룹의 run 폴더 증거에 있고 PR 본문이 적습니다.

## writer 지연(`writer_latency`, 11.5절)

10,000줄 journal에서 기록 하나의 비용입니다. 그 전 kit(`ebd10b6`, `/1` writer `core.add_note`)은 M 원본(`lab.json`)의 사본에, 이 헤드(`core.record`·
`Writer.update`)는 그 원본을 전환한 목적지(`analytics.json`, 같은 10,000줄에 `/2` 줄을 덧붙임)의 사본에 썼습니다. 종류마다 20회, kit마다 자기
프로세스에서 그 kit만 import하고, 두 kit을 번갈아 두 번 돌려 판마다 새 사본을 썼습니다. 값은 두 판의 최솟값입니다(ms).

| 기록 | 그 전 kit p50 / p95 | 이 헤드 p50 / p95 |
|---|---|---|
| 보통 기록(`note`) | 32.6 / 39.3 | 110.2 / 123.9 |
| 참조 넷을 이름으로 주는 기록(run id, 데이터셋 이름, 이벤트 id, `script.py::id`) | 31.7 / 37.9 | 152.0 / 159.9 |
| 열린 질문을 닫는 기록(`resolve`) | 68.7 / 79.1 | 127.5 / 136.1 |
| `record_update`(현재 finding을 철회, 잠금 안의 상태 재생) | — | 86.8 / 97.7 |

이 헤드의 writer는 기록 하나에 journal 전체를 세 번 파싱합니다(`relations.Journal`): 잠금 밖(결박과 줄 크기), 잠금 안(재판정과 상태 재생), 덧붙인
뒤(응답의 `targets`). 이름으로 준 참조가 있으면 읽기 모델도 읽습니다. 이 PR은 고치지 않았습니다. 설계 11.5절과 구현 주의 I8대로 seq·상태 색인은
#15의 인덱스와 함께 두고, 더 싼 한 걸음은 닫는 대상이 없는 기록에서 셋째 파싱을 건너뛰는 것입니다(리뷰 항목). 이 값은
[현재 계약](../CONTRACTS.md) G의 "비용"에 적었습니다.

## 진입점 행렬(`entry_points`, T8)

시험이 남긴 그대로입니다(`tests/test_marker_gate.py`, aelix 레그의 `tests/test_extension.py`). 칸이 비면 받아들여짐(쓰거나 읽음), 아니면 오류
코드이고, 거절·건너뜀인 칸은 프로젝트 트리(잠금 파일 포함)가 전후로 같습니다.

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
| 표식 없음 | `no_project` | `no_project` | — | — | `no_project` |
| 표식 없는 전환 목적지 | `migration_incomplete` | `migration_incomplete` | — | — | `source_not_legacy` |
| `lab.json` 프로젝트 아래의 `analytics.json` 디렉터리 | 씀 | 읽음 | 씀 | — | `source_not_legacy` / `inside_project` |

"—"는 그 열을 그 진입점으로 시험하지 않은 칸입니다(JSON의 열 목록이 시험한 것). 런처·`bin/analytics`의 행, run 안 SDK의 표식 바뀜, journal·run
잠금 안의 재판정은 시험 이름으로 JSON에 있습니다.

## 명령 E2E(`command_e2e`)

S fixture의 사본에 자기 `uv.lock`과 `.venv/`를 만들어(사람의 프로젝트처럼) 두 런처(`bin/analytics`, 설치본 `analytics`의 코드)를 실제 uv로
불렀습니다. 두 런처의 결과가 같습니다.

| 단계 | 결과 |
|---|---|
| `migrate --to D --dry-run` | 0, `dry_run: true`, D를 만들지 않음 |
| `migrate --to D` | 0(1.3초), 목적지의 `.venv/`를 `uv sync --locked`로 만듦, 목적지의 `uv.lock`이 원본과 같은 바이트, stderr `analytics: the project is D now, and the source is as it was (in aelix: /analytics use D)` |
| 다시 `migrate --to D` | 0, `already_migrated: true`, 사본 229개 `same`, `differs` 없음 |
| D에서 `status` / `record finding e2e` | 0 / 0, 쓴 줄은 `analytics.event/2`·`human`·`inferred`·`cli` |
| 원본에서 `record finding e2e` | 1, `migration_required` |
| 원본 재확인 뒤 `os._exit`로 끊은 목적지 | `status`가 1·`migration_incomplete`, `.venv/` 없음 |
| 그 목적지로 `migrate` | 0, 이어서 커밋, `.venv/`를 만듦 |
| 원본(`.venv/`·`uv.lock` 포함 5,738개)의 트리 | 처음과 끝이 같음 |

## 전환 다시 잼(`migration`, PR ②의 항목)

S·M fixture는 이 헤드의 `tools/eval_fixtures.py scale --scale S|M --seed 0 --kit-root <V1_KIT_COMMIT의 src>`로 만들었습니다(OQ7). 그 도구가 PR ②
뒤에 바뀌어 바이트와 digest가 PR ②의 fixture와 다릅니다(S 229개·2.9 MB·journal 300줄, M 2,037개·223 MB·journal 10,000줄). 전환은 모두 명령
`python -m aelix_analytics --project SRC migrate --to DEST`를 자기 프로세스로 부른 것입니다(런처가 uv 없이 부르는 것과 같음).

| 값 | S | M |
|---|---|---|
| 전체(두 번의 최솟값) | 1.02초 | 9.17초 |
| 복사 / 원본 재확인 / 검증 | 0.93 / 0.02 / 0.01초 | 8.58 / 0.29 / 0.18초 |
| 원본 잠금을 쥔 시간 | 0.97초 | 8.92초 |
| 최대 RSS(`/usr/bin/time -l`) | 31 MB | 76 MB |
| 게시(2단계 시험의 `probed.publish`) | `publish_new` | `publish_new` |
| 원본 digest(독립 걷기, 잠금 파일 둘 제외) | 전후 같음 | 전후 같음 |
| 두 전환의 사본 | 같음 | 같음 |
| 다시 부르기 | `already_migrated`, `same` 228 | `already_migrated`, `same` 2,036 |
| 보고서의 작성 출처 | `human`·`inferred`·`cli` | 같음 |
| 기록 비교(T11) | 참조 477·run 30·기록 상태 252 등 모든 판정이 같음 | 참조 15,000·run 300·기록 상태 9,633 등 모든 판정이 같음 |
| 목적지 `status` | `analytics.project/2`, 쓸 수 있음, 매핑 적용 | 같음 |
| 원본 `status` | `lab.project/1`, `migration_required`, `migration_hint`, 원본 그대로 | 같음 |

PR ②의 측정(S 1.01초·29 MB, M 9.40초·73 MB)과 같은 크기입니다. 중단 행렬(`fault_matrix`)은 F1–F16과 F15(링크 없이 사본의 직접 쓰기) 37행이
PR ②의 관측과 같고(F15의 정체성 전 끊김은 `destination_mismatch(occupied)`), 이 PR이 더한 행 넷 — 링크가 없는 목적지에서 표식을 `rename_new`로
게시하는 중에 끊김(`marker_begin`·`marker_temp`·`marker_written`, 게시 뒤) — 은 앞 셋이 이어서 커밋하고 마지막이 `already_migrated`이며, 모두 사본이
중단 없는 전환과 같고 원본 그대로, 임시 파일이 남지 않았습니다. 동시성(T5·T7·T15)과 T13·T10의 writer 시험, T11의 단위 시험은 이 헤드에서
통과했습니다(시험 이름은 JSON).

## uv(`uv`, T18)

`tests/test_migrate_command.py`가 실제 uv로 남긴 관찰입니다. 두 런처 모두 맞는 lock과 맞지 않는 lock에서 0으로 끝나고 원본의 `.venv/`·`uv.lock`과
목적지의 lock을 바꾸지 않습니다. 맞지 않는 lock은 sync가 실패하고 stderr가 `the migration is done, but preparing the environment of <dest>
failed (uv sync --locked, exit 1); the project's next command prepares it`입니다.

## aelix가 부른 CLI의 작성 주체(`agent_cli`, T22)

aelix 0.1.0b2가 자기 로더로 확장을 로드하고 harness를 만든 뒤(`on("user_bash")`는 harness의 hook 버스로 연결, 세션은 시험 디렉터리의 JSONL), kit
CLI를 aelix의 bash 도구와 `cli/repl.handle_user_bash`로 불렀습니다. CPython 3.12와 3.11의 결과가 같습니다.

| 경우 | 기록 |
|---|---|
| 에이전트의 bash 도구 | `agent`·`declared`·`cli`, 그 세션 |
| 사람의 `!` 명령(kit 호출), 다른 확장 없음 | `human`·`declared`·`cli`, 그 세션 |
| 사람의 `!echo T22`(kit 호출 아님) | 이 확장은 셸을 돌려주지 않음, aelix의 셸이 돎 |
| 셸을 돌려주는 다른 확장이 먼저 등록 | kit 명령은 이 확장의 셸(`PersonShell`)로 돌아 `human`, 다른 확장의 셸은 그 명령에서 돌지 않음. `echo`는 다른 확장의 셸 |
| 다른 확장이 나중에 등록 | kit 명령도 다른 확장의 셸로 돌아 `agent`(현재 계약 A의 한계), `echo`는 다른 확장의 셸 |
| 확장 자신의 kit 자식(`/analytics`) | `human`·`command`(환경의 `agent`와 무관) |

Windows의 PowerShell·cmd 자식은 실행하지 않았습니다(CI의 Windows aelix 레그가 돌립니다).

## 링크가 없는 파일 시스템(`foreign_fs`)

명령 그룹(G3)이 macOS 26.6.2의 FAT32·exFAT 디스크 이미지(fskit, nobrowse)에서 잰 것이며, 그 뒤 `src/`는 바뀌지 않았습니다. 두 볼륨 모두 `link`가
ENOTSUP이고, `renamex_np(RENAME_EXCL)`은 있는 이름에서 EEXIST(바이트 그대로)이며 없는 이름은 FAT32가 rename, exFAT이 ENOTSUP입니다. 출하된
전환은 두 볼륨 모두 1단계 `destination_invalid(not_empty)`로 멈췄습니다: macOS가 확장 속성이 없는 볼륨에서 파일마다 AppleDouble `._<이름>`을
만들기 때문입니다(소유자 질문). AppleDouble을 지나치게 한 실험 실행은 FAT32에서 `rename_new`로 표식을 게시해 커밋했고(사본 다섯은 직접 쓰기, 표식의
직접 쓰기 없음), exFAT에서는 2단계 `no_exclusive_rename`으로 멈췄습니다.

## 실행하지 않은 것

- 저장소 CI의 Linux·Windows·macOS 레그(push 전): Linux의 `renameat2`(ctypes), Windows의 `rename_new` = `publish_new`, 런처의 `--no-project`를
  Windows에서, Windows 전용 시험(`tests/test_windows_long_paths.py`는 실행하지 않고 고침), T22의 PowerShell·cmd 자식.
- 이 그룹에서 `AELIX_ANALYTICS_TEST_INSTALL=1`(배포본 end-to-end).
- 실제 이동식 FAT·exFAT 매체와 Linux·Windows의 FAT·exFAT.
- 전원 손실과 그 뒤의 내구성, Windows의 디렉터리 flush·읽기 전용 사본·잠금 해제 지연, 네트워크 파일 시스템.
- 실제 aelix 모델 세션(bash 도구와 `handle_user_bash`를 직접 부름), aelix 밖의 에이전트 호스트, aelix RPC의 `bash`.
- 색인이 있는 writer의 비용(#15).
