# 폴더 게시 설계 — 고정 build의 전달·확인·최신 주소 전환

- 상태: **설계 제안.** `analytics publish`는 아직 없다. 채택한 결정은 [ADR-0009](../decisions/0009-first-publish-target.md)(첫 대상: 사용자가 지정한 폴더)이고,
  이 문서는 그 결정이 "구현 PR이 정한다"로 남긴 것을 정하고 구현을 PR로 나눈다. 현재 공개 동작은 [현재 계약](../CONTRACTS.md)이 설명한다.
- 작업: [#21](https://github.com/handochan/aelix-analytics/issues/21) `[B4-s/P1]`. 완료 조건 1(첫 대상·접근 범위·실패/재시도 계약의 기록)은 ADR-0009로 채웠다.
  선행인 [#20](https://github.com/handochan/aelix-analytics/issues/20)(P0 독립 build)은 2026-10-27에 끝났다(kit 0.28.0).
- 기준: 코드는 `fb12b71`(main)이다. 구현 PR은 그때의 코드와 이 문서를 다시 대조한다.
- 따르는 결정: ADR-0009(아래 "결정 n", "확인 n"은 그 "구현에서 확인할 것"), [ADR-0006](../decisions/0006-common-record-contract.md)(기록 계약),
  [ADR-0014](../decisions/0014-publication-record-contract.md)(`publication` 결박·`content_seq`), [ADR-0003](../decisions/0003-portable-boundaries-and-in-process-kit-loading.md)
  (포트·이식 가능한 이름), [ADR-0007](../decisions/0007-line-endings.md)(UTF-8·LF), [공유 설계](../PUBLISH-AND-SHARING.md)(아래 PUBLISH) §3·§6·§8,
  [공유 build 설계](PUBLICATION-BUILD.md)(아래 BUILD).
- 소유자 결정: 12절의 OQ1–OQ7은 2026-10-27 소유자가 **권장안을 모두 채택**했다(대화, [#21](https://github.com/handochan/aelix-analytics/issues/21)에 기록).
  본문에서 "(OQn)"이 붙은 규칙은 그 결정이다. 새 공개 이름은 [용어집](../TERMINOLOGY-AND-LANGUAGE.md) §8대로 구현 PR이 먼저 등록한다.
- 리뷰: `7083597`의 설계 리뷰(독립 1·Codex 1)를 반영했다. 지적과 처분은 PR 본문의 표에 있다.

## 0. 요약

1. `analytics publish --build <publication_id> --to <폴더>`가 #20의 build 폴더를 대상 폴더에 전달한다. 다섯 단계(전송·확인·확정·최종 확인·전환)를
   결정 5대로 하고, 최신 포인터(`latest.json`과 channel 페이지)에는 최종 확인을 마친 버전만 쓴다.
2. 대조의 기준은 프로젝트 기록의 결박(`publication` 이벤트)이다. #20의 `checks.verify_package(dir, binding=…)`가 manifest의 ID·형식·두 해시·프로필·범위·
   분석 상태·검사 요약·기준을 결박과 맞춘다. 게시는 원본 프로젝트를 다시 읽지 않고, 결박과 같은 manifest 바이트가 적은 파일만 옮긴다.
3. 대상을 바꾸기 전에 시작 기록(새 kind `publish_attempt`)을 쓰고, 끝나면 `publish_receipt`(ADR-0006이 예약한 kind)를 쓴다. 그 전의 판정은 읽기만 한다.
   `publish_attempt`는 ADR-0014 결정 2의 닫힌 집합에 들어가고, 새 kind이므로 kit 버전과 writer 하한을 올린다.
4. 게시는 분석 라이브러리를 쓰지 않는다. `build --list`·`--verify`처럼 kit의 Python만으로 돈다(Python 3.11 floor 포함).
5. 새 포트는 없다. 기존 포트에 메서드 셋을 더한다(5절): `FileLocks.exclusive_within`(대기 상한), `AtomicFiles.rename_dir_new`(확정의 두 경우),
   `ProcessControl.machine_identity`(OQ4). ADR-0009 결정 5의 "기존 계약의 확장"이고 대체 구현을 주입한 계약 시험을 둔다(ADR-0003).
6. 구현은 PR 넷이다(11절): 이 설계와 기록 계약(PR-0), 대상·포트 확장(PR-1), 게시와 기록(PR-2), channel 페이지·되돌리기·검증(PR-3).

## 1. 범위

### 1.1 맡는 것과 맡지 않는 것

| 맡는다 | 맡지 않는다(근거) |
|---|---|
| 지정한 폴더 하나(로컬·마운트한 공유·동기화 폴더)로의 게시, 재시도·재사용, 최신 주소 전환, 되돌리기(`--switch-to`) | 원격 provider·실제 팀 접근 제어·자동 게시(P2, PUBLISH §8) |
| 게시 시도의 시작 기록과 receipt, 결과가 기록되지 않은 시도의 흔적 보고 | 동시 게시의 순서 보장, 네트워크 잠금·동기화 전파의 검증(결정 5, 검증하지 않는 범위) |
| channel 페이지(최신·이전 버전 링크, 버전 목록, 접근 범위 문구, 도착 확인 뒤 이동) | 독자 기기에서 버전 전체를 대조하는 수단(#48) |
| CI Linux 두 번째 계정의 읽기, HTTP 하위 경로, 세 OS의 중단 주입 | 실제 다른 독자의 열람(#24, 소유자) |
| `content_seq` 닫힌 집합, build 잠금의 대기 상한(BUILD 9.5 R18) | 게시본 삭제(사람이 지운다, 결정 6) |

### 1.2 이미 있는 것(`fb12b71`)

- **build 폴더와 결박.** `.analytics/publications/<publication_id>/`에 manifest(`publication.json`)와 그 파일이 있다. `publication` 이벤트의
  `details`가 `publication_id`·`package_sha256`(manifest 바이트의 sha256)·`location` 등을 담는다(`publishing/build.py` `build_publication`).
  `relations.publication_record(journal, id)`가 결박을 찾는다. 이 함수는 파일을 대조하지 않는다.
- **대조.** `checks.verify_package(path, binding=None)`가 manifest 형식·파일 목록·바이트 수·sha256·이름·링크를 검사하고 `package_sha256`을 돌려준다.
  `binding`을 주면 manifest의 필드를 결박의 필드와 직접 맞춘다. `publishing/state.py`는 그 인자를 넘기고 manifest의 `publication_id`가 요청한 ID인지 따로 본다.
  디렉터리의 일반 파일을 모두 읽으므로 manifest 밖 파일·빈 디렉터리·링크를 거절하고, `checks.OS_METADATA`의 이름만 따로 돌려준다. 첫 오류에서 멈추며,
  파일 목록이 다를 때의 오류는 어느 파일인지 담지 않는다(PR-1이 더한다).
- **확정 연산.** `AtomicFiles.rename_new(src, dst)`가 있다(#13·#19). 이름이 이미 있으면 빈 디렉터리여도 `FileExistsError`로 아무것도 바꾸지 않는다.
  POSIX는 `renameat2(RENAME_NOREPLACE)`(Linux)나 `renamex_np(RENAME_EXCL)`(macOS)를 부르고, 둘 다 없거나 파일 시스템이 플래그를 받지 않으면
  `RenameUnsupported`다. Windows는 교체 없는 이동이다. 다른 볼륨은 실패한다. 포트 설명은 "교체하는 rename으로 대신하지 않는다"고 적는다.
- **표식 만들기의 선례.** 전환은 표식을 `publish_new`(링크) → `rename_new` 순서로 만들고, 둘 다 안 되면 `no_exclusive_rename`으로 거절한다
  (`migration.py`의 `_publication`, 2026-10-08 소유자 결정).
- **잠금.** `FileLocks.exclusive`(상한 없이 기다림)와 `try_exclusive`(기다리지 않음, `LockBusy`·`LockUnavailable`, 디렉터리를 만들지 않음)가 있다.
  POSIX `try_exclusive`는 `flock`의 다른 오류(`ENOLCK`·`EOPNOTSUPP` 등)도 `LockUnavailable`로 내고, 링크인 잠금 파일은 따라가 연다.
- **빈 폴더의 판정.** `core.EMPTY_DIRECTORY_NAMES`와 `core.appledouble_name`이 OS가 만든 이름(`.DS_Store`, AppleDouble `._*`)을 빈 폴더의 일부로 본다.
- **journal 줄 상한.** 한 줄은 `writer.LINE_LIMIT`(50 KiB)보다 짧아야 하고, 넘으면 `text_too_long`이다.
- **없는 것.** 기기 식별자, `publish_attempt` kind, `publish_receipt`의 `details` 형식(지금 schema는 `publication_id`만 요구), 대상 폴더의 형식들, 명령.
- **로컬 설정.** 템플릿 `.gitignore`는 `.analytics/` 아래를 항목별로 무시한다(`.analytics/publications/` 포함). 기존 프로젝트에는 새 줄이 없다(OQ2).
- **누출 검사.** build는 게시 계열 이벤트를 스냅샷에서 먼저 빼고(`publishing/snapshot.py`), 환경 금지 값은 남은 스냅샷 항목의 `*path` 필드에서 모은다
  (`publishing/build.py`). 그래서 게시 기록에만 있는 대상 경로는 지금 금지 목록에 들지 않는다(6.4).

## 2. 대상 폴더의 형식

결정 4의 배치를 그대로 쓴다. 이름 철자는 이 문서가 정하고 PR-1이 용어집 §5에 등록한다.

| 파일 | 형식 | 내용 |
|---|---|---|
| `analytics-target.json` | `analytics.publish_target/1` | `schema`, `created_at`, `kit_version`. 없을 때만 만든다. 임시 파일을 쓴 뒤 `publish_new` → `rename_new`(전환과 같은 순서), 둘 다 안 되면 `no_exclusive_rename` |
| `.analytics-target.lock` | 빈 파일 | 잠금. 정리하지 않는다 |
| `publications/<id>/` | #20의 패키지 | manifest와 manifest가 적은 파일만. 한 번 쓰면 바꾸지 않는다 |
| `projects/<channel>/latest.json` | `analytics.publish_channel/1` | `schema`, `channel`, `project_id`, `project_title`, `latest`(publication_id), `history[]` |
| `projects/<channel>/index.html` | kit이 그린 페이지 | 최신 주소. 7절 |
| `.staging/<attempt_id>.json` | `analytics.publish_staging/1` | 소유 기록: `schema`, `attempt_id`, `project_id`, `publication_id`, `machine`, `host`, `user`, `pid`, `started_at`, `updated_at` |
| `.staging/<attempt_id>/` | 전송 사본 | 그 시도의 staging |

- `history[]`의 항목: `publication_id`, `package_sha256`, `content_sha256`, `renderer`, `build_seq`(결박 이벤트의 seq), `built_at`, `latest_since`(최신이 된 시각),
  `attempt_id`, `project_id`. channel 페이지는 `latest.json`만으로 그릴 수 있어야 한다.
- 소유 기록의 `machine`·`host`는 원값이 아니라 `sha256("aelix-analytics/publish\0" + 값)`의 앞 32자다. 대상의 독자는 staging도 읽을 수 있으므로(결정 8)
  기기 고유 값을 그대로 두지 않는다. `user`는 POSIX uid의 10진 문자열, Windows는 사용자 SID다. 모르는 값은 null이다.
- `attempt_id`는 `att-`와 소문자 ULID다(`publication_id`와 같은 생성기, 대소문자를 구별하지 않는 대상에서도 겹치지 않음).
- 모든 JSON은 UTF-8·LF, 키 정렬, 끝 줄바꿈 하나다(ADR-0007). 모르는 `schema`의 표식·`latest.json`은 읽지 않고 거절한다(`target_unsupported`).
- 메타데이터의 닫힌 목록(결정 4·6)은 #20의 `checks.OS_METADATA` 하나다(지금 `.DS_Store`·`desktop.ini`·`Thumbs.db`). 게시와 `--verify`가 같은 목록을 쓴다.
  이 이름은 같음 판단을 바꾸지 않고 receipt의 `observations.metadata`에 남는다. AppleDouble(`._*`)과 동기화 프로그램의 이름을 더할지는 PR-1이 macOS의
  SMB·FAT 복사와 Dropbox·OneDrive 폴더에서 재서 정하고, 더하면 `--verify`도 같이 바뀐다고 현재 계약에 적는다.
- 전송은 바이트만 쓴다(`copy2`·`copystat`를 쓰지 않는다). 권한 비트와 확장 속성을 옮기지 않으므로 새 파일은 대상 폴더와 게시한 계정의 기본 권한을 받고
  (결정 8), macOS가 확장 속성을 AppleDouble 파일로 만들 일이 없다.

## 3. 대상의 판정(결정 1·4)

판정은 대상에 아무것도 만들지 않는 읽기다. 시작 기록 전에 한 번, 잠금을 잡은 뒤에 다시 한 번 한다(4절). 걸리면 그 자리에서 끝난다
(`target_refused`, 이유 필드). 대상 폴더·잠금 파일·표식·관리 디렉터리는 판정이 아니라 시작 기록 뒤의 초기화가 만든다.

1. 경로: 받은 경로를 `Path.absolute()`로, 실제 위치를 `resolve(strict=False)`로 얻는다. 둘 다 receipt에 남는다.
2. 프로젝트와의 겹침: 프로젝트 루트와 대상 각각의 받은 경로·실제 위치 네 쌍 가운데 하나라도 한쪽이 다른 쪽의 조상(같음 포함)이면 거절(`overlaps_project`).
   비교는 OS 규칙의 대소문자(Windows·기본 macOS는 대소문자 무시)와 NFC로 한다. bind mount·`subst`를 거친 겹침을 `(st_dev, st_ino)`로 찾는 것은 ADR 밖의 후속이다.
3. 상위 표식: 대상의 조상에 `analytics-target.json`이 있으면 거절(`inside_target`).
4. 표식: 있으면 형식 버전을 확인한다. 없으면 대상이 없거나, 비어 있을 때만 새 대상이다. 비어 있음은 `core`의 빈 폴더 규칙(`EMPTY_DIRECTORY_NAMES`, AppleDouble)에
   더해 `.analytics-target.lock` 하나를 허용한다(kit 자신의 이름이며, 표식을 만들기 전에 끝난 시도가 남긴다). 그 밖은 거절(`not_empty`). 표식 없는
   `.staging/`은 사용자 것으로 본다(확인 6).
5. 관리 경로의 링크: 이미 있는 표식·잠금 파일·`publications`·`projects`·`.staging`과 그 아래 이 시도가 다룰 디렉터리가 링크·junction이면 거절(`managed_link`).
   대상 경로까지의 링크는 1에서 풀었다.
6. 볼륨: 이미 있는 `.staging`·`publications`의 `st_dev`가 서로 다르면 거절(`cross_volume`). 아직 없으면 초기화가 만든 뒤 본다.

## 4. 게시의 단계(결정 5·6)

```text
판정 → 결박 조회 → 시작 기록 → 대상 폴더 → 잠금 경로 판정 → 잠금 → 다시 판정 → 표식 → 관리 디렉터리
     → 이전 흔적 → 전송 → 확인 → 확정 → 최종 확인 → 전환 → 정리 → receipt
```

| 단계 | 하는 일 | 걸리면 |
|---|---|---|
| 판정 | 3절. 읽기만 한다 | 기록 없이 끝난다(대상·기록 무변경) |
| 결박 조회 | 이 프로젝트의 결박을 찾는다. 되돌리기는 결박이나 channel history(8절) | 일반 게시에서 결박이 없으면 `not_bound`, 기록 없이 끝난다 |
| 시작 기록 | 대상을 바꾸기 전에 `publish_attempt`를 쓴다(6.1) | 못 쓰면 끝난다(대상 무변경) |
| 대상 폴더 | 없으면 만든다 | receipt(`failure.step: "init"`) |
| 잠금 경로 판정 | `.analytics-target.lock`이 링크·junction이 아님을 본다(`try_exclusive`가 링크를 따라가므로 그 전에) | receipt(`managed_link`) |
| 잠금 | `FileLocks.exclusive_within(lock, 30초)`(OQ3). 넘으면 `target_busy`. 파일 시스템이 잠금을 지원하지 않으면(`ENOLCK`·`ENOTSUP`·`EOPNOTSUPP`, Windows `ERROR_NOT_SUPPORTED`) 잠금 없이 진행하고 `lock.state: "unsupported"` | receipt(`target_busy`, 그 밖의 잠금 실패는 `failure.step: "lock"`) |
| 다시 판정 | 잠금 안에서 3절을 다시 한다 | receipt(`target_refused`) |
| 표식 | 없으면 만든다(2절의 순서) | receipt(`no_exclusive_rename` 등) |
| 관리 디렉터리 | `publications`·`projects`·`.staging`을 만들고 3.6을 본다 | receipt(`cross_volume` 등) |
| 이전 흔적 | 6.3 | — |
| 전송 | 아래 "전송". 대상에 같은 ID의 버전이 있으면 대조해 맞으면 `reused`, 다르면 `version_differs`로 끝낸다 | receipt |
| 확인 | staging을 결박과 대조한다 | 전환하지 않고 receipt |
| 확정 | `AtomicFiles.rename_dir_new(staging, publications/<id>)`(5절) | "있음"이면 결정 6의 같은 ID 판단, 그 밖은 receipt |
| 최종 확인 | 옮긴 위치를 다시 대조한다 | 전환하지 않고 receipt(버전은 고치거나 지우지 않음) |
| 전환 | 8절 | 포인터 상태를 관찰해 receipt |
| 정리 | 자기 소유 기록과 남은 staging, 끝난 다른 시도(아래)를 지운다 | 결과를 receipt에 |
| receipt | 6.2. 대상의 파일이 먼저이고 receipt가 마지막이다 | 6.3 |

- 시작 기록 전에 끝나는 경우(판정의 거절, `not_bound`)는 대상과 기록을 바꾸지 않은 요청이며 시도가 아니다. CLI가 이유를 낸다. 시작 기록 뒤의 실패는
  모두 receipt가 짝을 이룬다.
- **전송.** (1) build 폴더의 `publication.json`을 링크를 따라가지 않고 일반 파일로 열어 읽고, 그 바이트의 sha256이 결박의 `package_sha256`과 다르면 아무것도
  쓰지 않고 `build_changed`로 끝난다. (2) 파일 목록은 그 바이트에서만 읽고, 각 경로가 #20의 이름 규칙(`checks.check_names`, `..`·절대 경로 없음)을 지키는지 본다.
  (3) 원본은 일반 파일만 열고(`lstat`로 확인, 가능한 OS에서 `O_NOFOLLOW`) 링크·특수 파일이면 `build_changed`다. (4) `.staging/<attempt_id>/` 아래에만 배타적으로
  만들어 쓰고(`open(…, "xb")`), manifest는 (1)의 바이트로 마지막에 쓴다. 쓰는 동안 소유 기록의 `updated_at`을 한 시간보다 짧은 간격으로 고친다.
- **대조.** 결박이 있으면 `verify_package(dir, binding=결박 이벤트의 details)`와 manifest `publication_id` == 요청 ID다(`state.py`와 같은 검사). 해시만 맞추지 않는다.
  결박이 없는 기기의 되돌리기는 history의 `package_sha256`과 manifest ID의 일치만 볼 수 있고, receipt의 `binding: "channel_history"`로 구별한다.
  대조가 `OSError`로 읽지 못하면 `unknown`이다(같은 ID의 다른 내용인 `version_differs`가 아니다).
- **다른 시도의 정리.** 다른 시도의 항목은 (a) 소유 기록의 `machine`·`host`·`user`가 모두 이 시도와 같고 `ProcessControl.alive(pid)`가 False이거나, (b) `updated_at`이
  24시간보다 오래됐을 때만 지운다. `alive`는 다른 사용자의 프로세스에도 False를 답하므로 사용자가 다르거나 모르면 (a)로 판정하지 않는다. 복제한 VM은 같은
  `machine`을 가질 수 있어 `host`를 함께 본다(호스트 이름만으로 같은 기기라고 하지 않는다, 결정 5). 소유 기록이 없거나 읽을 수 없는 항목은 수정 시각이
  24시간보다 오래됐을 때만 지운다. 표식·잠금 파일·이름 규칙에 맞지 않는 항목은 지우지 않고 `observations.kept`에 남긴다.
- **중단.** `KeyboardInterrupt`를 받으면 다음 단계를 시작하지 않고 자기 staging을 지우려 시도한 뒤 receipt를 쓴다. 전환 도중이면 포인터 상태를 관찰해 적는다.
- **결과가 불확실한 호출.** `rename_dir_new`·`replace`가 오류를 내도 관련 이름을 다시 읽어 판단한다(결정 5). 확정 뒤 staging이 없고 최종 위치가 결박과 맞으면
  확정된 것이다. 다시 읽지 못하면 그 단계는 `unknown`이고 전환하지 않는다.

## 5. 포트의 확장(결정 5, ADR-0003)

ADR-0009는 확정 연산과 잠금 대기 상한을 기존 `AtomicFiles`·`FileLocks` 계약의 확장으로 정했다. OS마다 다른 동작을 게시 코드에 두지 않고 기존 포트의
메서드로 더한다. 각 메서드는 포트 설명·현재 계약·대체 구현을 주입한 계약 시험을 함께 가진다(PR-1).

1. **`AtomicFiles.rename_dir_new(src, dst)`** — 디렉터리를 새 이름으로 옮긴다. 내용이 있는 항목(파일, 비어 있지 않은 디렉터리)이 `dst`에 있으면 아무것도
   바꾸지 않고 `FileExistsError`다. 결과의 방법(`rename_new`·`rename_new_after_rmdir`·`posix_rename`)을 돌려준다.
   - 먼저 `rename_new`를 부른다. `FileExistsError`이고 `dst`가 빈 디렉터리면 `rmdir`(비어 있을 때만 성공)한 뒤 한 번 다시 부른다. 잃는 내용이 없다(OQ5).
   - `RenameUnsupported`면 POSIX에서는 `os.rename`으로 대신한다. `rename(2)`는 디렉터리를 비어 있지 않은 디렉터리나 파일 위로 옮기지 않고(`ENOTEMPTY`·`EEXIST`·
     `ENOTDIR`, macOS는 errno 66), 이 오류들은 `FileExistsError`로 바꿔 낸다. 빈 디렉터리 위로는 옮기며 첫 경우와 같은 결과다(OQ5). 옮기는 것이 디렉터리일 때만
     이렇게 하므로 `rename_new`의 "교체하는 rename으로 대신하지 않는다"는 파일에 대해 그대로다. Windows의 `rename_new`는 교체하지 않으므로 대신할 일이 없다.
   - Windows에서 다른 프로세스(백신·색인·동기화 프로그램)가 연 파일 때문에 생기는 거절은 `replace`처럼 정해 둔 횟수만 다시 시도한다. 횟수는 PR-1이 재서 정한다(확인 8).
   - 다른 볼륨(`EXDEV`)과 그 밖의 오류는 실패로 끝난다. 복사로 대신하지 않는다.
2. **`FileLocks.exclusive_within(path, seconds)`** — `try_exclusive`를 짧은 간격으로 되풀이해 상한 안에 잡는다. 넘으면 `LockBusy`. 디렉터리를 만들지 않는다.
   build 잠금도 이것으로 바꾸고 넘으면 `build_busy`다(BUILD 9.5 R18, 지금의 `analytics build` 동작을 바꾸므로 현재 계약 D3를 고친다).
3. **`ProcessControl.machine_identity()`** — Linux `/etc/machine-id`(없으면 `/var/lib/dbus/machine-id`), macOS `ioreg -rd1 -c IOPlatformExpertDevice`의
   `IOPlatformUUID`, Windows 레지스트리 `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`를 읽는다. 못 얻으면 None이다(OQ4). 저장은 2절의 해시로만 한다.

## 6. 기록(결정 7) — PR-0의 기록 계약

### 6.1 `publish_attempt`(새 kind, 시작 기록)

- `text`: `Publishing <publication_id> to <대상 이름 또는 폴더 이름>`. 경로 전체는 `details`에만 둔다.
- `refs`: publication 참조 하나(ADR-0006 3절, schema의 `reference`). 결박 조회가 시작 기록 앞이므로 쓸 때 해시를 안다.
  - 이 프로젝트에 결박이 있으면 `{type: "publication", publication_id, relation: "about", basis: "observed", version_binding: "author",
    digest: {sha256: 결박의 package_sha256}}`이고, 읽기에서 `resolved`다.
  - 결박이 없는 되돌리기(다른 기기)는 `basis: "declared"`이고 digest는 history의 `package_sha256`이다. 읽기는 `missing`(`record_absent`)이며 그것이 이 기기에
    결박이 없다는 사실이다. writer는 쓰는 시점에 참조의 형식만 검사한다(`writer.append`의 `reference_problem`).
- `details`: `attempt_id`, `publication_id`, `action`(`publish`·`switch`), `target`(`{name, path, resolved}`, `name`은 저장한 대상이 아니면 null),
  `channel`(이름, 아직 정하지 못했으면 null), `started_at`.

### 6.2 `publish_receipt`(예약 kind의 첫 형식)

- `text`: `Published <id> to <대상>: <요약>` 또는 `Publish of <id> to <대상> <outcome> at <단계>: <코드>`.
- `refs`: 시작 기록과 같다.
- `details`의 필드: `attempt_id`, `publication_id`, `action`, `outcome`, `package_sha256`(대조한 해시, 대조 전에 끝나면 null), `binding`(`project_record`·
  `channel_history`·`none`), `target`(`{name, path, resolved, marker_created}`), `channel`(`{name, reassigned_from, previous_latest}`), `started_at`, `ended_at`,
  `steps`(아래), `failure`(`{step, code, message, paths[]}`), `commit_method`, `lock`(`{state, error}`), `pointers`(`latest_json`·`channel_page` 각각
  `old`·`new`·`other`·`missing`·`unknown`), `addresses`(`version`·`latest`, 게시한 기기의 `file://` URL), `observations`(`metadata[]`, `kept[]`,
  `unrecorded_attempts[]`, `remote_links`), `access`(9.2), `reports[]`(`{report_id, revision_id}`).
- `steps`: `transfer`·`check`·`commit`·`final_check`·`switch` 다섯 키가 늘 있다. 값은 `done`·`reused`(전송만)·`exists`(확정만)·`already`(전환만: 이미 이 버전)·
  `not_newer`·`superseded`(전환만)·`failed`·`interrupted`·`unknown`·`not_started`다.
- `outcome`은 단계에서 정해진다. 우선순위: 단계 가운데 `unknown`이 하나라도 있으면 `unknown` → `interrupted` → `failed` → 전환의 `not_newer`·`superseded` →
  `published`(전환 `done`·`already`). 재사용은 `outcome`이 아니라 `steps.transfer: "reused"`로 보인다.
- **늘 있어야 하는 필드**: `attempt_id`, `publication_id`, `action`, `outcome`, `binding`, `package_sha256`(null 허용), `target`, `started_at`, `ended_at`, `steps`, `lock`.
  모르는 값은 생략하지 않고 `unknown`이나 null로 적는다.
- **조건부로 있어야 하는 필드**(schema의 `if/then`): `outcome`이 `failed`·`interrupted`·`unknown`이면 `failure`. `steps.switch`가 `not_started`가 아니면 `pointers`
  (두 키 모두)와 `channel`. `steps.commit`이 `done`·`exists`면 `commit_method`. `outcome`이 `published`·`not_newer`·`superseded`면 `addresses`와 `access`.
- **크기.** receipt는 journal 한 줄이다(`writer.LINE_LIMIT` 50 KiB). 원격 링크 목록은 manifest의 `remote_links`가 `package_sha256`으로 고정하므로 receipt에는
  `remote_links: {count, source: "manifest"}`만 둔다. 그 밖의 목록(`failure.paths`, `metadata`, `kept`, `unrecorded_attempts`)은 모두 담되, 줄이 상한을 넘으면
  각 목록을 앞 20개와 `<목록>_count`·`truncated: true`로 줄여 다시 맞춘다. 결과(`outcome`·`steps`·`pointers`)는 줄이지 않는다.

### 6.3 결과가 기록되지 않은 시도와 receipt 실패

- 시작 기록만 있고 같은 `attempt_id`의 receipt가 없는 시도는 ADR-0006 6절의 축으로 읽는다: `recording_status: "incomplete"`, 이유 `no_completion_event`.
  성공으로 읽지 않는다. Workspace Timeline은 "Publish result not recorded"로 보인다(OQ7).
- 이 프로젝트가 같은 대상에 다시 게시하면, 정리하거나 전환하기 전에 그런 시도 가운데 **이 대상의 어느 receipt도 아직 보고하지 않은 것**의 흔적(그 `attempt_id`의
  소유 기록·staging, 그 버전, 포인터가 그 시도의 것인지)을 `observations.unrecorded_attempts`에 적는다. 보고한 시도는 다시 싣지 않으므로 목록이 쌓이지 않는다.
  이전 기록은 고치지 않는다(결정 7).
- receipt를 쓰지 못하면 명령은 대상의 결과(주소)와 기록 실패(`receipt_not_recorded`)를 함께 내고 실패(exit 1)로 끝난다. 그 시도는 시작 기록만 남은 시도로 읽힌다.

### 6.4 형식의 바깥 규칙

- schema: `analytics-records.schema.json`의 kind enum에 `publish_attempt`를 더하고, 두 kind의 `details`를 6.1·6.2의 필수·조건부 필드로 검사한다.
- `content_seq`: `basis.PUBLICATION_KINDS`와 ADR-0014 결정 2의 닫힌 집합에 `publish_attempt`를 더한다. PR-2가 ADR-0014 결정 2의 "지금은 두 kind뿐" 문장과
  ADR-0006의 B4 kind 표를 함께 고친다. 이전 kit(0.28.0 이하)은 이 kind를 모르는 줄로 읽어 제외하지 않는다(보수적: 내용 기준이 늦어질 뿐이다).
- writer 하한: PR-2가 `KIT_VERSION`을 올리고, 첫 `publish_attempt`를 쓰기 전에 표식의 `min_writer_version`을 그 버전으로 올린다(ADR-0006 1.3, `requires=`,
  build의 `PUBLICATION_WRITER_SINCE`와 같은 방식).
- **build의 누출 금지 값.** 게시 계열 kind는 공유 투영에 들어가지 않는다(ADR-0014 결정 2). 그러나 지금 build는 그 이벤트를 스냅샷에서 먼저 빼므로 `details`에만
  있는 대상 경로가 금지 목록에 들지 않는다(1.2). PR-2는 빼기 전에 게시 계열 이벤트 `details`의 절대 경로와 `file://` URL을 `snapshot.private_values`에 모으고
  (BUILD 11.4 "기록에 남은 모든 절대 경로"), 그 경로를 발견 글에 옮긴 build가 `leak_found`로 멈추는 회귀 시험을 둔다.

## 7. channel 페이지(결정 4)

- kit이 `latest.json`으로 그리는 정적 HTML이다. 영어 UI다(#10). #20 페이지처럼 CSP meta와 `data:` 아이콘을 두되, 자기 inline script의 해시로 `script-src`를
  정한다(#20의 CSP를 그대로 쓰면 자기 스크립트가 막힌다).
- 담는 것: 최신 버전과 바로 이전 버전으로 가는 링크(`../../publications/<id>/index.html`), 버전 목록(최신이 된 시각, build 시각, 이웃 버전과 `renderer`가 같고
  `content_sha256`이 같으면 "same content", ADR-0014 결정 1), 소유 프로젝트 제목, 접근 범위 문구 "Anyone who can open this folder can read every version in it.",
  기계가 읽을 `<meta name="analytics-latest">`. 가리키는 버전이 목록에서 가장 새 build가 아니면 "This channel points to an older build, chosen by switching"을 보인다.
- 도착 확인 뒤 이동: 페이지 스크립트가 최신 버전의 entry가 직접 불러오는 자원 가운데 실행하지 않고 불러오기 성공을 알 수 있는 것(스타일시트·이미지)을
  `link`·`img` 요소로 불러 보고, 모두 `load`이면 `location.replace`로 옮긴다. 최신 버전의 스크립트를 channel 페이지 안에서 실행하지 않는다. 하나라도 `error`이거나
  그런 자원이 없으면 옮기지 않고 "The newest version may not have fully arrived on this device" 안내와 이전 버전 링크를 보인다. 스크립트가 꺼진 독자는
  `<noscript>` 안의 meta refresh로 옮긴다. 자원 종류는 PR-3이 세 엔진 × `file://`·HTTP에서 재서 고정한다(결정 4). 네트워크 API는 쓰지 않는다.
- channel 페이지는 버전 디렉터리 밖이므로 버전의 내용 해시에 들지 않는다. 전환할 때마다 다시 그린다.

## 8. 전환과 되돌리기(결정 5)

- 전환 직전에 `latest.json`을 다시 읽는다. 시작할 때 본 값과 다르면 `superseded`로 끝낸다(바꾸지 않음). 이미 이 시도의 버전이면 `already`이고 channel 페이지만 맞춘다.
- 일반 게시: `latest.json`이 없거나 이 build의 `build_seq`가 지금 최신 항목의 `build_seq`보다 클 때만 바꾼다. 비교는 같은 `project_id`끼리만 하고, 지금 최신의
  항목에 `build_seq`가 없거나 `project_id`가 다르면 새 build로 보지 않는다(`not_newer`). `--reassign-channel`은 비교하지 않는다.
- 교체 순서: `latest.json`(`replace`) → channel 페이지(`replace`). 임시 파일 이름은 `.<attempt_id>.latest.json.tmp`처럼 `attempt_id`를 담는다(정리 규칙).
- 두 포인터가 다르면(앞 시도가 그 사이에 멈춤) 이 channel의 다음 게시가 `latest.json` 기준으로 channel 페이지를 다시 그린다. 그 전에 `latest.json`이 가리키는 버전을
  이 기기에서 대조한다(결박이 있으면 결박, 없으면 history의 `package_sha256`). 대조하지 못하면 다시 그리지 않고 receipt에 적는다. 동기화 폴더에서 아직 다 도착하지
  않은 버전을 channel 페이지에 넣지 않기 위해서다.
- 되돌리기 `--switch-to <id>`: 파일 전달 없이 그 버전을 대조한 뒤 전환만 한다(이 기기에 결박이 있으면 결박, 없으면 history의 `package_sha256`. 둘 다 있는데 다르면
  거절). 고를 수 있는 것은 그 channel의 history에 있는 버전이나, 이 프로젝트의 결박이 있고 대상에 확정된 버전이다. 되돌리기도 시작 기록과 receipt를 쓰고
  (`action: "switch"`), receipt의 `transfer`·`check`·`commit`은 `not_started`다.

## 9. 대상·channel·접근

### 9.1 channel(결정 4)

- 지정하지 않으면 이 프로젝트가 그 대상에 가진 channel(`latest.json`의 `project_id`가 같은 것)을 쓴다. 없으면 제목의 slug, slug가 비면 `project-` + `project_id`의
  hex 부분(`prj_` 뒤 16자) 앞 8자로 만든다. 이 프로젝트의 channel이 둘 이상이면 `channel_ambiguous`로 거절한다.
- 이름 규칙: NFC로 만들고, 비교는 NFC + `casefold()`다. 이식할 수 없는 이름은 #20의 `checks.check_names` 규칙으로 거절한다. 비교로 같은 디렉터리가 둘 이상이면 거절.
- 소유: `projects/<channel>/`이 없거나, 메타데이터·publisher 임시 파일만 있거나, `latest.json`의 `project_id`가 이 프로젝트일 때만 쓴다(그 밖은 `channel_owned`).
  다른 프로젝트의 channel은 `--reassign-channel`만 넘겨받고, receipt의 `channel.reassigned_from`과 `previous_latest`를 적는다. history의 이전 항목은 지우지 않는다.
  publisher가 쓰지 않은 파일이 있는 channel 디렉터리는 `--reassign-channel`로도 넘겨받지 않는다.
- 확인 7의 읽기: 같은 slug의 두 프로젝트는 같은 channel 이름이 되어 두 번째 게시가 `channel_owned`로 거절된다. 빈 slug의 두 프로젝트는 `project_id`에서 서로 다른
  이름을 받으므로 겹치지 않는다. 같은 `project_id`의 복사본(빈 slug 포함)은 같은 이름이 되며, 결정 4대로 같은 channel의 소유자로 본다.

### 9.2 접근 범위(결정 8)

- 화면·CLI·receipt 어디에도 "team only"를 쓰지 않는다. CLI는 끝에 "Anyone who can open <folder> can read every version in it."을 적는다.
- receipt의 `access`: `reread`(확인·최종 확인의 다시 읽기 결과), `posix`(대상까지의 상위 디렉터리와 버전 디렉터리·파일의 group·other의 `x`·`r` 비트 관찰, POSIX만),
  `windows_acl: "not_checked"`. 다른 사용자가 읽을 수 없어 보이면 경고(`others_cannot_read`)만 한다. 권한을 바꾸지 않는다.

### 9.3 저장한 대상(결정 3, OQ2)

- `--target <이름>`은 git이 무시하는 로컬 설정의 이름이다. `.analytics/publish-targets.json`(`analytics.publish_targets/1`, `{name: path}`).
- 만들기는 `analytics publish --save-target <이름> --to <폴더>`다. 3절 판정만 하고 대상에 아무것도 만들지 않는다.
- PR-3은 템플릿 `.gitignore`에 그 줄을 더한다. 전환(`migration.py`)은 바꾸지 않는다. 그 줄이 없는 프로젝트(이전 프로젝트, 전환한 프로젝트)에서는 저장하지 않고
  줄을 더하라고 안내한다. 그래서 저장한 대상은 언제나 git이 무시하는 곳에 있다.

## 10. 명령과 출력

```text
analytics publish --build <publication_id> (--to <폴더> | --target <이름>) [--channel <이름>] [--reassign-channel]
analytics publish --switch-to <publication_id> (--to <폴더> | --target <이름>) [--channel <이름>]
analytics publish --save-target <이름> --to <폴더>
analytics publish --list [--to <폴더> | --target <이름>]
```

- 출력은 build처럼 JSON 요약(`ok`, `outcome`, `steps`, `addresses`, receipt의 id·seq)이다. 사람용 한 줄 요약을 stderr에 함께 낸다.
- `--list`: 이 프로젝트의 시도(시작 기록·receipt 짝)와 결과, 대상을 주면 그 대상의 channel·history를 보인다. 대상을 바꾸지 않는다.
- 오류 코드: `not_bound`, `build_changed`, `version_differs`, `target_refused`(이유), `target_unsupported`, `target_busy`, `no_exclusive_rename`, `channel_ambiguous`,
  `channel_owned`, `receipt_not_recorded`. `not_newer`·`superseded`는 오류가 아니라 결과다.
- `/analytics publish …`가 같은 CLI를 부른다. 상시 모델 도구는 없다(결정 2). 확장은 build처럼 하위 프로세스로 돌린다.

## 11. PR 나눔과 검증

| PR | 내용 | 리뷰 | 끝났다는 증거 |
|---|---|---|---|
| PR-0 | 이 설계와 기록 계약(6절), 용어집 등록 목록, ADR-0009·ADR-0014와의 대조 | 기록 형식의 설계 리뷰(독립 1·Codex 1) | 문서 검사, 리뷰 처분, 소유자 OQ 답 |
| PR-1 | 포트 확장 셋(5절)과 그 계약 시험, build 잠금의 대기 상한(`build_busy`, D3), 대상 판정(3절)·형식(2절)·channel 규칙(9.1), `verify_package`의 파일 목록 오류에 경로 | 일반(독립 1·Codex 1) | 세 OS CI, 확인 8(Windows의 열린 파일 측정 포함) |
| PR-2 | 게시 단계(4절)와 기록(6절), CLI의 `--build`·`--list`(10절), 중단·불확실 호출·흔적·정리, kit 버전·writer 하한·schema·reader·Workspace 표시, 누출 금지 값(6.4) | 기록 형식(구현 리뷰 + 수정 diff 확인) | 세 OS CI, 측정 기록(md+JSON) |
| PR-3 | channel 페이지(7절)·되돌리기(8절)·저장한 대상(9.3), CI의 두 번째 계정·HTTP 하위 경로·SMB 루프백·도착 확인 자원 측정, 현재 계약 G·README | 일반 | CI, 검증 기록, Q14의 #21 몫 결과표 |

ADR-0009 "구현에서 확인할 것"의 담당:

| 확인 | 담당 |
|---|---|
| 1 Linux 두 번째 계정·HTTP 하위 경로 | PR-3(CI Linux 필수 단계) |
| 2 macOS·Windows 두 번째 계정, Windows 루프백 SMB | PR-3(CI 단계로 시도, 막히면 not_run, OQ6) |
| 3 중단 주입 | PR-2(다섯 단계의 경계, `latest.json` 앞뒤), PR-3(두 포인터 사이, 빌드 A·B와 되돌리기) |
| 4 재사용·거절·manifest 밖 파일·메타데이터 | PR-2. "되돌리기는 결박과 대조한 뒤에만"은 PR-3 |
| 5 게시 중 원본·빌드 변경 | PR-2 |
| 6 대상의 소유와 경계 | PR-1(판정), PR-2(게시 경로 전체) |
| 7 channel | PR-1(이름·소유 규칙), PR-2(게시의 거절), PR-3(`--reassign-channel`의 receipt) |
| 8 확정 연산 | PR-1(세 OS와 대체 구현 주입, Windows의 열린 파일), PR-3(SMB 루프백) |
| 9 잠금 | PR-1(대기 상한, `LockBusy`·지원하지 않음을 주입한 계약 시험), PR-2(겹친 두 게시), PR-3(실제 마운트에서 잴 수 있으면 SMB 루프백, 아니면 not_run) |
| 10 receipt 전 강제 종료, receipt 실패 | PR-2. channel 페이지가 끼는 경우는 PR-3 |
| 11 channel 페이지 | PR-3 |
| 12 실제 다른 독자 | #24 |

## 12. 소유자 결정(2026-10-27, 권장안 모두 채택)

| | 질문 | 권장안 | 대안 |
|---|---|---|---|
| OQ1 | 시작 기록의 kind와 receipt `details`(6절) | `publish_attempt`와 6.2의 필드 | 시작 기록 없이 receipt만(결정 7과 어긋남) |
| OQ2 | 저장한 대상(`--target`)을 베타에 넣을지와 위치 | 넣는다. `.analytics/publish-targets.json`, `--save-target`으로 만들고, `.gitignore` 줄이 없으면 저장하지 않음 | 베타에서는 `--to`만(결정 2·3을 좁힘) |
| OQ3 | 대상 잠금과 build 잠금의 대기 상한 | 30초 | 10초, 120초 |
| OQ4 | 기기 식별자 | 세 OS에서 읽는다(Linux `/etc/machine-id`, macOS `IOPlatformUUID`, Windows `MachineGuid`). 못 얻으면 null이고 다른 기기로 본다 | 늘 null(다른 시도의 staging은 24시간 뒤에만 지움) |
| OQ5 | 확정의 두 경우(5절) | 빈 디렉터리는 비운 뒤 다시, `RenameUnsupported`는 POSIX `rename`으로 대신 | `rename_new`만(플래그 없는 마운트에 게시 불가) |
| OQ6 | macOS·Windows 두 번째 계정과 Windows 루프백 SMB(확인 2) | CI 단계로 시도해 결과대로 적는다. 러너가 막으면 그 사실을 `not_run`으로 | 처음부터 소유자 수동 |
| OQ7 | Workspace의 게시 표시 | Timeline에 시작 기록·receipt를 보이고, receipt 없는 시도는 "result not recorded" | 표시하지 않음(`--list`만) |

- 리뷰 뒤의 한계: 표식을 만들 때는 링크(`publish_new`)나 플래그 있는 rename(`rename_new`)이 있어야 한다(전환과 같은 규칙). 둘 다 없는 마운트에는 새 대상을 만들 수
  없고(`no_exclusive_rename`), 다른 곳에서 만든 표식이 있는 대상에는 OQ5의 대신하는 이동으로 게시할 수 있다. PR-1이 측정한 마운트를 현재 계약 G에 적는다.
