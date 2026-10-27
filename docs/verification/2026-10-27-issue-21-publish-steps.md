# Issue #21 PR-2 — 폴더 게시의 단계·기록

[PR #137](https://github.com/handochan/aelix-analytics/pull/137)의 측정 기록이다. 원시 수치·명령·소스 해시는 [JSON](2026-10-27-issue-21-publish-steps.json)에 있다.
설계는 [폴더 게시 설계](../design/PUBLISH-FOLDER.md) §4·§6·§8, 결정은 [ADR-0009](../decisions/0009-first-publish-target.md), 현재 동작은
[현재 계약 D6](../CONTRACTS.md#d6-폴더-게시-kit-0300-21-pr-2)이다.

## 기준

`tools/publish_eval.py`는 clean `2c7e11e`(kit 0.30.0)에서 돌렸다. 전체 시험은 `11ba5d7`에서 돌렸고, 두 커밋의 차이는 그 도구가 포인터를 읽는 순서뿐이다.
그 파일을 읽는 시험은 `2c7e11e`에서 다시 돌렸다. 기기는 macOS 26.6.2 arm64(18코어, 64 GB), CPython 3.12.13이다. 기기 기록은
`tools/eval_fixtures.py environment` 형식이다.

| 무엇 | 결과 |
|---|---|
| 로컬 전체 시험(`11ba5d7`) | **3022 passed, 139 skipped**, 613.39초 |
| 로컬 전체 시험(`374009b`, 제품 코드가 같은 마지막 커밋) | 3022 passed, 139 skipped, 615.32초 |
| 도구 수정 뒤 해당 시험(`2c7e11e`: 줄 끝·kit 해시·비밀 값 검사·계층·kit·게시) | 337 passed |
| CI 세 OS(필수 검사 일곱 개) | 아래 표 |

앞선 전체 시험은 다음과 같다.

- `0280a14`: 1건 실패(2994 passed). 목록인 `kind`가 새 시도 색인을 멈췄다. 독립 리뷰도 찾았고 `95b9697`이 고쳤다.
- `95b9697`: 3016 passed.
- `2a9196f`: 1건 실패(3020 passed). 시험이 도는 동안 추적 파일을 고쳐 kit 해시 시험이 실패했다. 제품의 실패가 아니며, `374009b`에서는 통과했다.

## 측정(`tools/publish_eval.py`)

- **작은 프로젝트**(파일 9개, 다섯 번의 중앙값):

  | 경우 | 결과 | 시간 |
  |---|---|---|
  | 첫 게시 | `published`·전송 `done` | 0.038초 |
  | 같은 build 다시 | `published`·전송 `reused`·전환 `already` | 0.019초 |
  | 더 새 build | `published` | 0.038초 |
  | 더 오래된 build | `not_newer`·`reused` | 0.018초 |

- **경계 중단**(설계 4절의 여덟 경계, 첫 게시와 이후 게시 각각, 16개):
  - 모두 `interrupted` receipt가 남았다.
  - receipt의 포인터 상태는 실제 파일과 같았다. 전환 전에 멈춘 경우는 receipt에 포인터가 없고, 파일은 `missing`(첫 게시)이거나 `old`였다.
  - 새 버전을 가리키는 포인터는 `channel_page` 경계(`latest.json`만 교체됨)에서만 나왔고, 그때는 최종 확인이 끝나 있었다.
  - 다시 실행하면 16개 모두 `published`로 끝났다. 전송은 확정 전이면 `done`, 확정 뒤면 `reused`였다.
- **두 프로세스가 동시에 게시**(오래된·새 build, 각 20쌍):

  | 대상 잠금 | 종료 | 결과 | 최대 시간 |
  |---|---|---|---|
  | 있음 | 40개 모두 exit 0·receipt | 최신은 늘 새 build(오래된 쪽 11번 `not_newer`) | 0.099초 |
  | 없음(주입) | 40개 모두 exit 0·receipt | 최신은 새 build 16번·오래된 build 4번(`superseded` 4번). 앞의 16번 중 1번은 `latest.json`이 새 build, 페이지가 오래된 build로 갈렸다 | 0.067초 |

  - 잠금 없는 쪽의 순서와 갈림은 설계가 받아들인 한계다(동시 게시의 순서 비보장). 갈린 포인터는 다음 게시가 대조한 뒤 맞춘다.
  - 두 경우 모두 남은 staging은 0, stderr도 0이었다.
- **M 규모**: #20 PR5의 M 사본은 v1에서 옮겼고 build가 두 개 있다. 이 두 build를 한 폴더에 게시했다.

  | 경우 | 내용 | 결과 | 시간 |
  |---|---|---|---|
  | 첫 build | read: 426파일, 12,839,471 B | `published` | 2.86초 |
  | 둘째 build | evidence: 1,612파일, 20,589,469 B | `published` | 5.16초 |
  | 첫 build 다시 | 대조 후 재사용 | `not_newer` | 1.44초 |

  receipt는 2.7–2.9 KB로 줄 상한 50 KiB 안이다. 전체 도구 실행은 58.90초, 최대 메모리 300 MB였다.
- **exFAT**(macOS의 exFAT 디스크 이미지):
  - 새 폴더에 두 번 게시했다. 두 번 모두 시작 기록과 `failed` receipt가 남았다(`init`·`no_exclusive_rename`).
  - 폴더에는 빈 잠금 파일과 그 AppleDouble만 남았다. 둘 다 판정이 kit의 흔적으로 보므로 두 번째 시도도 같은 이유로 끝났다(`not_empty`가 아님).
  - PR-1의 측정대로 이 볼륨에는 새 대상을 만들 수 없다.

## 측정에서 찾아 고친 것

잠금 없이 두 게시를 낸 첫 측정에서 10쌍 가운데 1쌍의 한 게시가 시작 기록 전에 `not_empty`로 거절됐다. 판정이 표식이 없다고 본 직후, 다른 시도가
표식과 관리 폴더를 만들었기 때문이다. `374009b`가 그 경우 표식을 읽게 고쳤고, 그 뒤 40쌍에서 0건이었다(`tests/test_publish_target.py`).
도구의 첫 실행(`11ba5d7`)은 경계 중단의 포인터를 다시 실행한 뒤에 읽었다. `2c7e11e`가 순서를 고쳤고 위 수치는 고친 뒤의 것이다.

## 리뷰와 처분

기록 형식 PR이므로 고정 커밋 `124837e`에서 구현 리뷰(독립 1·Codex 1)를 하고, 수정 `95b9697`에 수정 diff 확인(Codex) 한 번을 했다.

| 리뷰 | 지적 | 처분 |
|---|---|---|
| Codex 구현 리뷰 | must-fix 8 | 모두 고침(`95b9697`) |
| 독립 구현 리뷰 | must-fix 10·minor 11·follow-up 8 | must-fix·minor는 모두 고침(`95b9697`). 시험 공백 일부와 follow-up 3개(9.2 경고, 흔적의 channel, `--list --to`)는 PR-3 |
| 수정 diff 확인 | must-fix 3(모두 수정이 만든 것) | 고침(`2a9196f`) |

- 수정 diff 확인의 must-fix 3은 다음과 같다.
  1. 이미 최신인 버전의 페이지를 그리다 멈추면 receipt가 거절됐다.
  2. 그 뒤 페이지를 다시 읽지 못해도 성공으로 적었다.
  3. 손으로 고친 단계 값이 receipt 검사를 멈췄다.
- 다시 리뷰하지 않았다(리뷰 예산). 그 뒤의 변경 `374009b`(판정의 경합)와 도구 두 커밋은 이 측정과 전체 시험이 확인했다.

## 미실행

- 이 도구를 Linux·Windows에서 돌리지 않았다. CI는 두 프로세스 시험을 포함한 시험만 돌린다.
- 두 번째 OS 계정·HTTP 하위 경로·SMB 루프백(PR-3), 네트워크 공유·동기화 폴더.
- aelix 안의 `/analytics publish`와 설치한 런처. `bin/analytics`는 이 checkout에서 실행했다.
- Windows ACL 관찰(설계 9.2대로 `windows_acl: not_checked`).
- p50/p95를 위한 반복 측정. M 경우는 한 번씩 쟀다.
- 두 기기에서 동시에 게시하는 경우.
