# 폴더 게시 설계 — 고정 build의 전달·확인·최신 주소 전환

- 상태: **설계 제안.** `analytics publish`는 아직 없다. 채택한 결정은 [ADR-0009](../decisions/0009-first-publish-target.md)(첫 대상: 사용자가 지정한 폴더)이고,
  이 문서는 그 결정이 "구현 PR이 정한다"로 남긴 것을 정하고 구현을 PR로 나눈다. 현재 공개 동작은 [현재 계약](../CONTRACTS.md)이 설명한다.
- 작업: [#21](https://github.com/handochan/aelix-analytics/issues/21) `[B4-s/P1]`. 완료 조건 1(첫 대상·접근 범위·실패/재시도 계약의 기록)은 ADR-0009로 채웠다.
  선행인 [#20](https://github.com/handochan/aelix-analytics/issues/20)(P0 독립 build)은 2026-10-27에 끝났다(kit 0.28.0).
- 기준: 코드는 `fb12b71`(main)이다. 구현 PR은 그때의 코드와 이 문서를 다시 대조한다.
- 따르는 결정: ADR-0009(아래 "결정 n"), [ADR-0006](../decisions/0006-common-record-contract.md)(기록 계약), [ADR-0014](../decisions/0014-publication-record-contract.md)
  (`publication` 결박·`content_seq`), [ADR-0003](../decisions/0003-portable-boundaries-and-in-process-kit-loading.md)(포트·이식 가능한 이름),
  [ADR-0007](../decisions/0007-line-endings.md)(UTF-8·LF), [공유 설계](../PUBLISH-AND-SHARING.md)(아래 PUBLISH) §3·§6·§8, [공유 build 설계](PUBLICATION-BUILD.md)(아래 BUILD).
- 소유자 결정: 12절의 OQ1–OQ7은 2026-10-27 소유자가 **권장안을 모두 채택**했다(대화, [#21](https://github.com/handochan/aelix-analytics/issues/21)에 기록).
  본문에서 "(OQn)"이 붙은 규칙은 그 결정이다. 새 공개 이름은 [용어집](../TERMINOLOGY-AND-LANGUAGE.md) §8대로 구현 PR이 먼저 등록한다.

## 0. 요약

1. `analytics publish --build <publication_id> --to <폴더>`가 #20의 build 폴더를 대상 폴더에 전달한다. 다섯 단계(전송·확인·확정·최종 확인·전환)를
   결정 5대로 하고, 최신 포인터(`latest.json`과 channel 페이지)에는 최종 확인을 마친 버전만 쓴다.
2. 대조의 기준은 프로젝트 기록의 결박(`publication` 이벤트의 `package_sha256`)이다. 대조 함수는 #20의 `checks.verify_package`를 그대로 쓴다.
   게시는 원본 프로젝트를 다시 읽지 않고, build 폴더에서 manifest가 적은 파일만 옮긴다.
3. 시도마다 시작 기록(새 kind `publish_attempt`)을 대상을 바꾸기 전에 쓰고, 끝나면 `publish_receipt`(ADR-0006이 예약한 kind)를 쓴다.
   두 kind는 게시 계열이므로 ADR-0014 결정 2의 닫힌 집합에 `publish_attempt`를 더한다. 새 kind를 쓰므로 kit 버전과 writer 하한을 올린다.
4. 게시는 분석 라이브러리를 쓰지 않는다. `build --list`·`--verify`처럼 kit의 Python만으로 돈다(Python 3.11 floor 포함).
5. 새 포트는 없다. 확정은 기존 `AtomicFiles.rename_new`, 포인터 교체는 `AtomicFiles.replace`, 잠금 대기 상한은 기존 `FileLocks.try_exclusive`를
   되풀이하는 도움 함수로 한다. 기기 식별자만 `_platform`에 작은 함수를 더한다(OQ4).
6. 구현은 PR 넷이다(11절): 이 설계와 기록 계약(PR-0), 대상·포트 확장(PR-1), 게시와 기록(PR-2), channel 페이지·되돌리기·검증(PR-3).

## 1. 범위

### 1.1 맡는 것과 맡지 않는 것

| 맡는다 | 맡지 않는다(근거) |
|---|---|
| 지정한 폴더 하나(로컬·마운트한 공유·동기화 폴더)로의 게시, 재시도·재사용, 최신 주소 전환, 되돌리기(`--switch-to`) | 원격 provider·실제 팀 접근 제어·자동 게시(P2, PUBLISH §8) |
| 게시 시도의 시작 기록과 receipt, 결과가 기록되지 않은 시도의 흔적 보고 | 동시 게시의 순서 보장, 네트워크 잠금·동기화 전파의 검증(결정 5, 검증하지 않는 범위) |
| channel 페이지(최신·이전 버전 링크, 버전 목록, 접근 범위 문구, 도착 확인 뒤 이동) | 독자 기기에서 버전 전체를 대조하는 수단(#48) |
| CI Linux 두 번째 계정의 읽기, HTTP 하위 경로, 세 OS의 중단 주입 | 실제 다른 독자의 열람(#24, 소유자) |
| `content_seq` 닫힌 집합과 build 잠금의 대기 상한(BUILD 9.5 R18) | 게시본 삭제(사람이 지운다, 결정 6) |

### 1.2 이미 있는 것(`fb12b71`)

- **build 폴더와 결박.** `.analytics/publications/<publication_id>/`에 manifest(`publication.json`)와 그 파일이 있다. `publication` 이벤트의
  `details`가 `publication_id`·`package_sha256`(manifest 바이트의 sha256)·`location`을 담는다(`publishing/build.py` `build_publication`).
  `relations.publication_record(journal, id)`가 결박을 찾는다.
- **대조.** `checks.verify_package(folder_or_files)`가 manifest 형식·파일 목록·바이트 수·sha256·이름·링크를 검사하고 `package_sha256`을 돌려준다.
  결박과의 비교는 호출자가 한다(`publishing/state.py`가 그렇게 쓴다).
- **확정 연산.** `AtomicFiles.rename_new(src, dst)`가 있다(#13·#19). 이름이 이미 있으면 빈 디렉터리여도 `FileExistsError`로 아무것도 바꾸지 않는다.
  POSIX는 `renameat2(RENAME_NOREPLACE)`(Linux)나 `renamex_np(RENAME_EXCL)`(macOS)를 부르고, 둘 다 없거나 파일 시스템이 플래그를 받지 않으면
  `RenameUnsupported`다. Windows는 교체 없는 이동이다. 다른 볼륨은 실패한다(복사로 대신하지 않음). Report 저장이 staging → 불변 버전에 쓴다.
- **잠금.** `FileLocks.exclusive`(상한 없이 기다림)와 `try_exclusive`(기다리지 않음, `LockBusy`·`LockUnavailable`)가 있다. POSIX `try_exclusive`는
  `flock`의 다른 오류(`ENOLCK`·`EOPNOTSUPP` 등)도 `LockUnavailable`로 낸다.
- **없는 것.** 기기 식별자(결정 5의 정리 규칙), `publish_attempt` kind, `publish_receipt`의 `details` 형식(지금 schema는 `publication_id`만 요구),
  대상 폴더의 형식들, `analytics publish` 명령.
- **로컬 설정.** 템플릿 `.gitignore`는 `.analytics/` 아래를 항목별로 무시한다(`.analytics/publications/` 포함). 기존 프로젝트에는 새 줄이 없다(OQ2).

## 2. 대상 폴더의 형식

결정 4의 배치를 그대로 쓴다. 이름 철자는 이 문서가 정하고 PR-1이 용어집 §5에 등록한다.

| 파일 | 형식 | 내용 |
|---|---|---|
| `analytics-target.json` | `analytics.publish_target/1` | `schema`, `created_at`, `kit_version`. 없을 때만 만든다(`rename_new`로 배타적 생성) |
| `.analytics-target.lock` | 빈 파일 | 잠금. 정리하지 않는다 |
| `publications/<id>/` | #20의 패키지 | manifest와 manifest가 적은 파일만. 한 번 쓰면 바꾸지 않는다 |
| `projects/<channel>/latest.json` | `analytics.publish_channel/1` | `schema`, `channel`, `project_id`, `latest`(publication_id), `history[]` |
| `projects/<channel>/index.html` | kit이 그린 페이지 | 최신 주소. 7절 |
| `.staging/<attempt_id>.json` | `analytics.publish_staging/1` | 소유 기록: `schema`, `attempt_id`, `project_id`, `publication_id`, `machine`(없으면 null), `pid`, `started_at`, `updated_at` |
| `.staging/<attempt_id>/` | 전송 사본 | 그 시도의 staging |

- `history[]`의 항목: `publication_id`, `package_sha256`, `build_seq`(결박 이벤트의 seq), `built_at`, `latest_since`(최신이 된 시각), `attempt_id`, `project_id`.
- `attempt_id`는 `att-`와 소문자 ULID다(`publication_id`와 같은 생성기, 대소문자를 구별하지 않는 대상에서도 겹치지 않음).
- 모든 JSON은 UTF-8·LF, 키 정렬, 끝 줄바꿈 하나다(ADR-0007). 모르는 `schema`의 표식·`latest.json`은 읽지 않고 거절한다(`target_unsupported`).
- 메타데이터의 닫힌 목록(결정 4·6)은 #20의 `checks.OS_METADATA` 하나다(지금 `.DS_Store`·`desktop.ini`·`Thumbs.db`). `verify_package`가 이미 이 이름을
  따로 모으므로 게시와 `--verify`가 같은 목록을 쓴다. 이 이름은 같음 판단을 바꾸지 않고 receipt의 `observations.metadata`에 남는다. AppleDouble(`._*`)과
  동기화 프로그램의 이름을 더할지는 PR-1이 macOS의 SMB·FAT 복사와 Dropbox·OneDrive 폴더에서 재서 정하고, 더하면 `--verify`도 같이 바뀐다고 현재 계약에 적는다.

## 3. 대상의 판정(결정 1·4)

순서대로 판정하고 하나라도 걸리면 아무것도 쓰지 않는다(`target_refused`, 이유 필드).

1. 경로: 받은 경로를 `Path.absolute()`로, 실제 위치를 `resolve(strict=False)`로 얻는다. 둘 다 receipt에 남는다.
2. 프로젝트와의 겹침: 프로젝트 루트와 대상 각각의 받은 경로·실제 위치 네 쌍 가운데 하나라도 한쪽이 다른 쪽의 조상(같음 포함)이면 거절(`overlaps_project`).
   비교는 OS 규칙의 대소문자(Windows·기본 macOS는 대소문자 무시)와 NFC로 한다.
3. 상위 표식: 대상의 조상에 `analytics-target.json`이 있으면 거절(`inside_target`).
4. 표식: 있으면 형식 버전을 확인한다. 없으면 대상이 없거나, 비어 있거나, 메타데이터 목록만 있을 때만 새 대상이다. 그 밖은 거절(`not_empty`).
5. 관리 경로의 링크: 표식·잠금·`publications`·`projects`·`.staging`과 그 아래 이 시도가 다룰 디렉터리가 링크·junction이면 거절(`managed_link`).
   대상 경로까지의 링크는 1에서 풀었다.
6. 볼륨: `.staging`과 `publications`의 `st_dev`가 다르면 거절(`cross_volume`). 없으면 만든 뒤 확인한다.

## 4. 게시의 단계(결정 5·6)

```text
잠금 → 시작 기록 → 이전 흔적 관찰 → 전송 → 확인 → 확정 → 최종 확인 → 전환 → 정리 → receipt
```

| 단계 | 하는 일 | 상태 값 |
|---|---|---|
| 잠금 | `.analytics-target.lock`을 대기 상한(OQ3) 안에서 잡는다. 넘으면 `target_busy`. 파일 시스템이 잠금을 지원하지 않는다(`ENOLCK`·`ENOTSUP`·`EOPNOTSUPP`, Windows는 `ERROR_NOT_SUPPORTED`)면 잠금 없이 진행하고 `lock: {state: "unsupported", error}`를 적는다 | `held`·`unsupported`·`busy`·`failed` |
| 시작 기록 | 3절 판정을 통과한 뒤, 대상의 내용을 바꾸기 전에 `publish_attempt`를 쓴다. 못 쓰면 끝낸다(대상 무변경) | — |
| 이전 흔적 | 이 프로젝트의 시작 기록 가운데 receipt가 없는 시도를 찾고, 그 `attempt_id`의 소유 기록·staging·버전·포인터를 관찰해 `observations.unrecorded_attempts`에 적는다 | — |
| 전송 | 결박을 찾는다(없으면 `not_bound`). 대상에 같은 ID의 버전이 있으면 대조해 맞으면 `reused`, 다르면 `version_differs`로 끝낸다. 없으면 소유 기록을 쓰고 build 폴더에서 manifest가 적은 파일만 `.staging/<attempt_id>/`로 복사한다(manifest는 마지막) | `done`·`reused`·`failed`·`interrupted`·`not_started` |
| 확인 | staging을 결박과 대조한다(아래) | `done`·`failed`·… |
| 확정 | `rename_new(staging, publications/<id>)`. 5절의 두 경우 | `done`·`exists`·`failed`·`unknown` |
| 최종 확인 | 옮긴 위치를 다시 대조한다 | `done`·`failed`·`unknown` |
| 전환 | 8절 | `done`·`not_newer`·`superseded`·`failed`·`interrupted`·`unknown` |
| 정리 | 자기 소유 기록과 남은 staging, 이름 규칙에 맞는 오래된 다른 시도(결정 5)를 지운다 | 결과 목록 |
| receipt | 9절. 대상의 파일이 먼저이고 receipt가 마지막이다 | — |

- **대조.** `checks.verify_package(dir)`를 부르고 그 `package_sha256`이 결박과 같은지 본다. `verify_package`는 디렉터리의 모든 일반 파일을 읽어
  manifest와 맞추므로 manifest 밖 파일·빈 디렉터리·링크를 이미 거절하고, 메타데이터 목록의 이름만 따로 돌려준다(`checks.py`의 `walk`).
  build 폴더의 지금 파일로 판단하지 않는다(전송이 복사한 바이트를 staging에서 다시 읽는다).
- **중단.** `KeyboardInterrupt`를 받으면 다음 단계를 시작하지 않고 자기 staging을 지우려 시도한 뒤 receipt를 쓴다. 전환 도중이면 포인터 상태를 관찰해 적는다.
- **결과가 불확실한 호출.** `rename_new`·`replace`가 오류를 내도 관련 이름을 다시 읽어 판단한다(결정 5). 다시 읽지 못하면 `unknown`이고 전환하지 않는다.

## 5. 확정 연산의 두 경우(결정 5)

ADR-0009는 확정을 "빈 디렉터리 하나면 그 자리에 옮기고, POSIX는 `rename(2)`가 비어 있지 않은 디렉터리 위로 옮기지 않는 것에 기댄다"로 정했다. 지금의
`rename_new`는 그보다 엄격하다. 그래서 게시 쪽에서 둘을 맞춘다(OQ5).

1. **빈 디렉터리가 이미 있음.** `rename_new`가 `FileExistsError`를 내면 그 이름이 빈 디렉터리인지 보고, 그렇다면 `rmdir`(비어 있을 때만 성공)한 뒤 한 번
   다시 부른다. 잃는 내용이 없다. 그 밖의 `FileExistsError`는 "있음"이고 결정 6의 같은 ID 판단(대조해 재사용 또는 `version_differs`)으로 간다.
2. **`RenameUnsupported`.** 플래그를 받지 않는 파일 시스템(일부 SMB·NFS 마운트)이다. 옮기는 것이 디렉터리이므로 POSIX에서는 `os.rename`으로
   대신한다. `rename(2)`는 디렉터리를 비어 있지 않은 디렉터리나 파일 위로 옮기지 않는다(`ENOTEMPTY`·`EEXIST`·`ENOTDIR`). 빈 디렉터리 위로는 옮기며,
   이는 1과 같은 결과다. receipt의 `commit_method`가 `rename_new`·`rename_new_after_rmdir`·`posix_rename` 가운데 무엇인지 적는다.

## 6. 기록(결정 7) — PR-0의 기록 계약

### 6.1 `publish_attempt`(새 kind, 시작 기록)

- `text`: `Publishing <publication_id> to <대상 이름 또는 폴더 이름>`. 경로 전체는 `details`에만 둔다.
- `refs`: `[{type: "publication", publication_id, relation: "about"}]`(ADR-0006의 publication 참조).
- `details`: `attempt_id`, `publication_id`, `action`(`publish`·`switch`), `target`(`{name, path, resolved}`, `name`은 저장한 대상이 아니면 null),
  `channel`(이름, 아직 정하지 못했으면 null), `started_at`.

### 6.2 `publish_receipt`(예약 kind의 첫 형식)

- `text`: `Published <id> to <대상>: <요약>` 또는 `Publish of <id> to <대상> failed at <단계>: <코드>`.
- `refs`: 시작 기록과 같다.
- `details`(필수는 굵게): **`attempt_id`**, **`publication_id`**, **`action`**, **`outcome`**(`published`·`reused`·`not_newer`·`superseded`·`failed`·`interrupted`),
  `package_sha256`, `binding`(`project_record`·`channel_history`), `target`(`{name, path, resolved, marker_created}`), `channel`(`{name, reassigned_from, previous_latest}`),
  `started_at`, `ended_at`, **`steps`**(`transfer`·`check`·`commit`·`final_check`·`switch` 각각의 상태, 4절), `failure`(`{step, code, message, paths[]}` 또는 null),
  `commit_method`, `lock`(`{state, error}`), `pointers`(`latest_json`·`channel_page` 각각 `old`·`new`·`other`·`missing`·`unknown`), `addresses`(`version`·`latest`,
  게시한 기기의 `file://` URL), `observations`(`metadata[]`, `kept[]`, `unrecorded_attempts[]`, `remote_links[]`), `access`(9.2), `reports[]`(`{report_id, revision_id}`).
- 경로·URL은 기록한 기기의 절대 경로다(ADR-0006의 프로젝트 밖 경로). 그래서 build의 누출 정책이 이 값들을 이미 금지 목록에 넣는다(BUILD 11.4 "기록에 남은 모든
  절대 경로"). 게시 계열 kind는 공유 투영에 들어가지 않는다(ADR-0014 결정 2).

### 6.3 형식의 바깥 규칙

- schema: `analytics-records.schema.json`의 kind enum에 `publish_attempt`를 더하고, 두 kind의 `details` 필수 필드를 `if/then`으로 검사한다.
- `content_seq`: `basis.PUBLICATION_KINDS`와 ADR-0014 결정 2의 닫힌 집합에 `publish_attempt`를 더한다. 이전 kit(0.28.0 이하)은 이 kind를 모르는 줄로 읽어
  제외하지 않는다(보수적: 내용 기준이 늦어질 뿐이다).
- writer 하한: PR-2가 `KIT_VERSION`을 올리고, 첫 `publish_attempt`를 쓰기 전에 표식의 `min_writer_version`을 그 버전으로 올린다(ADR-0006 1.3,
  `requires=` 인자, build의 `PUBLICATION_WRITER_SINCE`와 같은 방식).
- 결과가 기록되지 않은 시도: 시작 기록만 있고 같은 `attempt_id`의 receipt가 없는 시도다. reader와 Workspace는 성공으로 읽지 않고 "result not recorded"로 보인다(OQ7).
- receipt를 쓰지 못하면 명령은 대상의 결과(주소)와 기록 실패를 함께 내고 실패(exit 1)로 끝난다. build의 결박처럼 다시 읽어 확인하지는 않는다. 시작 기록이
  이미 있으므로 다음 게시가 흔적을 적는다(결정 7).

## 7. channel 페이지(결정 4)

- kit이 `latest.json`으로 그리는 정적 HTML이다. #20 페이지와 같은 CSP·`data:` 아이콘을 쓰고, 영어 UI다(#10).
- 담는 것: 최신 버전과 바로 이전 버전으로 가는 링크(`../../publications/<id>/index.html`), 버전 목록(최신이 된 시각, build 시각, `content_sha256`이 같은 이웃이면
  "same content"), 소유 프로젝트 제목, 접근 범위 문구 "Anyone who can open this folder can read every version in it.", 기계가 읽을 `<meta name="analytics-latest">`.
  가리키는 버전이 목록에서 가장 새 build가 아니면(되돌림) "This channel points to an older build, chosen by switching" 줄을 보인다.
- 도착 확인 뒤 이동: 페이지 스크립트가 최신 버전의 entry가 직접 불러오는 자원(스타일시트·스크립트·이미지)을 `link`·`script`·`img` 요소로 불러 보고 모두
  `load`이면 `location.replace`로 옮긴다. 하나라도 `error`이거나 그런 자원이 없으면 옮기지 않고 "The newest version may not have fully arrived on this device"
  안내와 이전 버전 링크를 보인다. 스크립트가 꺼진 독자는 `<noscript>` 안의 meta refresh로 옮긴다. 자원 종류는 PR-3이 세 엔진 × `file://`·HTTP에서 재서
  고정한다(결정 4). 네트워크 API(`fetch` 등)는 쓰지 않는다(#20의 출하 JS 규칙).
- channel 페이지는 버전 디렉터리 밖이므로 버전의 내용 해시에 들지 않는다. 전환할 때마다 다시 그린다.

## 8. 전환과 되돌리기(결정 5)

- 전환 직전에 `latest.json`을 다시 읽는다. 시작할 때 본 값과 다르면 `superseded`로 끝낸다(바꾸지 않음). 이미 이 시도의 버전이면 channel 페이지만 맞춘다.
- 일반 게시: `latest.json`이 없거나 이 build의 `build_seq`가 지금 최신 항목의 `build_seq`보다 클 때만 바꾼다. 비교는 같은 `project_id`끼리만 하고, 지금 최신의
  항목에 `build_seq`가 없거나 `project_id`가 다르면 새 build로 보지 않는다(`not_newer`). `--reassign-channel`은 비교하지 않는다.
- 교체 순서: `latest.json`(`replace`) → channel 페이지(`replace`). 각각 임시 파일 이름은 `.<attempt_id>.latest.json.tmp`처럼 `attempt_id`를 담는다(정리 규칙).
- 두 포인터가 다르면(앞 시도가 그 사이에 멈춤) 이 channel의 다음 게시가 `latest.json` 기준으로 channel 페이지를 다시 그린다.
- 되돌리기 `--switch-to <id>`: 파일 전달 없이 그 버전을 대조(이 기기에 결박이 있으면 결박, 없으면 history의 `package_sha256`. 둘 다 있는데 다르면 거절)한 뒤
  전환만 한다. 고를 수 있는 것은 그 channel의 history에 있는 버전이나, 이 프로젝트의 결박이 있고 대상에 확정된 버전이다.

## 9. 대상·channel·접근

### 9.1 channel(결정 4)

- 지정하지 않으면 이 프로젝트가 그 대상에 가진 channel(`latest.json`의 `project_id`가 같은 것)을 쓴다. 없으면 제목의 slug, slug가 비면 `project_id`의 앞 8자로 만든다.
  둘 이상이면 `channel_ambiguous`로 거절한다.
- 이름 규칙: NFC로 만들고, 비교는 NFC + `casefold()`다. 이식할 수 없는 이름은 #20의 `checks.check_names` 규칙으로 거절한다. 비교로 같은 디렉터리가 둘 이상이면 거절.
- 소유: `projects/<channel>/`이 없거나, 메타데이터·publisher 임시 파일만 있거나, `latest.json`의 `project_id`가 이 프로젝트일 때만 쓴다. 다른 프로젝트의
  channel은 `--reassign-channel`만 넘겨받고, receipt의 `channel.reassigned_from`과 `previous_latest`를 적는다. history의 이전 항목은 지우지 않는다.

### 9.2 접근 범위(결정 8)

- 화면·CLI·receipt 어디에도 "team only"를 쓰지 않는다. CLI는 끝에 "Anyone who can open <folder> can read every version in it."을 적는다.
- receipt의 `access`: `reread`(확인·최종 확인의 다시 읽기 결과), `posix`(대상까지의 상위 디렉터리와 버전 디렉터리·파일의 group·other의 `x`·`r` 비트 관찰, POSIX만),
  `windows_acl: "not_checked"`. 다른 사용자가 읽을 수 없어 보이면 경고(`others_cannot_read`)만 한다. 권한을 바꾸지 않는다.

### 9.3 저장한 대상(결정 3, OQ2)

- `--target <이름>`은 git이 무시하는 로컬 설정의 이름이다. 권장안: `.analytics/publish-targets.json`(`analytics.publish_targets/1`, `{name: path}`). 만들기는
  `analytics publish --save-target <이름> --to <폴더>`(게시 없이 3절 판정만). 템플릿 `.gitignore`와 전환이 그 줄을 더하고, 그 줄이 없는 기존 프로젝트에서는
  저장하지 않고 줄을 더하라고 안내한다.

## 10. 명령과 출력

```text
analytics publish --build <publication_id> (--to <폴더> | --target <이름>) [--channel <이름>] [--reassign-channel]
analytics publish --switch-to <publication_id> (--to <폴더> | --target <이름>) [--channel <이름>]
analytics publish --save-target <이름> --to <폴더>
analytics publish --list [--to <폴더> | --target <이름>]
```

- 출력은 build처럼 JSON 요약(`ok`, `outcome`, `steps`, `addresses`, `receipt`의 id·seq)이다. 사람용 한 줄 요약을 stderr에 함께 낸다.
- `--list`: 이 프로젝트의 시도(시작 기록·receipt 짝)와 결과, 대상을 주면 그 대상의 channel·history를 보인다. 대상을 바꾸지 않는다.
- 실패 코드: `not_bound`, `version_differs`, `target_refused`(이유), `target_unsupported`, `target_busy`, `channel_ambiguous`, `channel_owned`, `not_newer`(실패가 아닌
  결과), `superseded`(결과), `receipt_not_recorded`.
- `/analytics publish …`가 같은 CLI를 부른다. 상시 모델 도구는 없다(결정 2). 확장은 build처럼 하위 프로세스로 돌린다.
- build 잠금도 같은 대기 상한을 쓰고 넘으면 `build_busy`다(BUILD 9.5 R18).

## 11. PR 나눔과 검증

| PR | 내용 | 리뷰 | 끝났다는 증거 |
|---|---|---|---|
| PR-0 | 이 설계와 기록 계약(6절: `publish_attempt`·`publish_receipt`의 필드, 닫힌 집합, writer 하한), 용어집 등록 목록, ADR-0009·ADR-0014와의 대조 | 기록 형식의 설계 리뷰(독립 1·Codex 1) | 문서 검사, 리뷰 처분, 소유자 OQ 답 |
| PR-1 | 대상 판정(3절)·형식(2절)·channel 규칙(9.1)·확정의 두 경우(5절)·잠금 대기 도움 함수·기기 식별자(OQ4), 각 계약 시험 | 일반(독립 1·Codex 1) | 세 OS CI, 확정 연산 시험(결정 확인 8) |
| PR-2 | 게시 단계(4절)와 기록(6절), CLI(10절), 중단·불확실 호출·흔적·정리, kit 버전·writer 하한·schema·reader·Workspace 표시 | 기록 형식(구현 리뷰 + 수정 diff 확인) | 세 OS CI의 중단 주입(결정 확인 3·4·5·6·9·10), 측정 기록(md+JSON) |
| PR-3 | channel 페이지(7절)·되돌리기(8절)·저장한 대상(9.3)·`--list`, CI Linux 두 번째 계정·HTTP 하위 경로·도착 확인 자원의 측정, 현재 계약 G·README | 일반 | CI, 검증 기록, Q14의 #21 몫 결과표 |

ADR-0009 "구현에서 확인할 것"과의 대응: 1(Linux 두 번째 계정)·11(channel 페이지)은 PR-3, 2(macOS·Windows 두 번째 계정, Windows 루프백 SMB)는 OQ6,
3–7·9·10은 PR-2, 8은 PR-1, 12는 #24다.

## 12. 소유자 결정(2026-10-27, 권장안 모두 채택)

| | 질문 | 권장안 | 대안 |
|---|---|---|---|
| OQ1 | 시작 기록의 kind와 receipt `details`(6절) | `publish_attempt`와 6.2의 필드 | 시작 기록 없이 receipt만(결정 7과 어긋남) |
| OQ2 | 저장한 대상(`--target`)을 베타에 넣을지와 위치 | 넣는다. `.analytics/publish-targets.json`, `--save-target`으로 만들고, `.gitignore` 줄이 없으면 저장하지 않음 | 베타에서는 `--to`만(결정 2·3을 좁힘) |
| OQ3 | 대상 잠금과 build 잠금의 대기 상한 | 30초 | 10초, 120초 |
| OQ4 | 기기 식별자 | 세 OS에서 읽는다(Linux `/etc/machine-id`, macOS `IOPlatformUUID`, Windows `MachineGuid`). 못 얻으면 null이고 다른 기기로 본다 | 늘 null(다른 시도의 staging은 24시간 뒤에만 지움) |
| OQ5 | 확정의 두 경우(5절) | 빈 디렉터리는 비운 뒤 다시, `RenameUnsupported`는 POSIX `rename`으로 대신 | `rename_new`만(플래그 없는 마운트에 게시 불가) |
| OQ6 | macOS·Windows 두 번째 계정과 Windows 루프백 SMB(결정 확인 2) | CI 단계로 시도해 결과대로 적는다. 러너가 막으면 그 사실을 `not_run`으로 | 처음부터 소유자 수동 |
| OQ7 | Workspace의 게시 표시 | Timeline에 시작 기록·receipt를 보이고, receipt 없는 시도는 "result not recorded" | 표시하지 않음(`--list`만) |
