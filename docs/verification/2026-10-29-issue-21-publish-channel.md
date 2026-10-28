# Issue #21 PR-3 — channel 페이지·되돌리기·저장한 대상

[PR #138](https://github.com/handochan/aelix-analytics/pull/138)의 측정 기록이다. 원시 수치·명령·소스 해시는 [JSON](2026-10-29-issue-21-publish-channel.json)에 있다.
설계는 [폴더 게시 설계](../design/PUBLISH-FOLDER.md) §7·§8·§9.2·§9.3, 결정은 [ADR-0009](../decisions/0009-first-publish-target.md), 현재 동작은
[현재 계약 D7](../CONTRACTS.md#d7-channel-페이지되돌리기저장한-대상-kit-0310-21-pr-3)과 G다. 앞 PR의 기록은 [PR-2](2026-10-27-issue-21-publish-steps.md)다.

## 기준

측정 도구·브라우저 시험·전체 시험은 clean `7286182`(kit 0.31.0)에서 돌렸다. 기기는 macOS 26.6.2 arm64(18코어, 64 GB), CPython 3.12.13이다. 기기 기록은
`tools/eval_fixtures.py environment` 형식이다. 다른 계정의 읽기는 CI `f5825e4`의 결과다. 그 커밋과 `7286182`는 접근 도구와 그 시험이 같다.
`7286182`는 시험 하나, 사용법 두 줄, 문서를 더했다.

| 무엇 | 결과 |
|---|---|
| 로컬 전체 시험(`7286182`) | **3051 passed, 140 skipped**, 611.09초 |
| 브라우저 시험 세 엔진(`tests/test_publish_browser.py`, Chromium 156·Firefox 157·WebKit 27.2) | 18 passed |
| CI 세 OS(필수 검사 일곱 개) | `f5825e4` 성공, 아래 표 |

고정 커밋 `7823374`의 전체 시험은 3건이 실패했다(3041 passed). template 규칙의 집합 시험, 현재 계약의 버전 문장, 인코딩이 없는 subprocess였고
`d96776a`가 고쳤다.

| CI 실행 | 커밋 | 결과 |
|---|---|---|
| 40713376229 | `7823374` | 실패: 세 platform 레그에서 위의 3건. Windows는 접근 도구 시험(이 계정의 읽기)도 실패했고, `net user /add`가 15자 이상 암호의 Y/N 질문에서 멈췄다. Linux·macOS의 두 번째 계정과 Windows 루프백 SMB 게시는 통과 |
| 40721743265 | `86cff5f` | 실패(Windows): 계정은 만들었지만 `Start-Process -Credential`이 세 경우 모두 180초 안에 끝나지 않았다(`not_run`). 이 계정의 읽기는 PowerShell 7 아래의 Windows PowerShell 5.1이 `Get-FileHash`를 불러오지 못해 실패 |
| 40732331281 | `f5825e4` | 성공. 전체 시험 Linux 3059 passed, macOS 3060 passed, Windows 2862 passed. Linux 브라우저 13 passed. 세 OS의 두 번째 계정이 읽음 |

## 다른 계정의 읽기(`tools/publish_access_check.py`, ADR-0009 확인 1·2·8·9)

CI가 두 번째 OS 계정을 만들고 두 build를 게시한 뒤, 그 계정으로 `latest.json`, channel 페이지, 최신 버전 manifest의 모든 파일을 sha256으로 읽었다.
읽은 프로세스가 보고한 uid나 계정 이름이 그 계정이어야 통과다. 닫힌 대상은 접근 거부만 통과다.

| OS | 계정 | 열린 대상 | 닫힌 대상 | 루프백 SMB |
|---|---|---|---|---|
| Linux(`ubuntu-24.04`) | `useradd`, uid 1002 | pass: 8파일, 14,124 B | pass: `PermissionError`, 게시가 `others_cannot_read` 경고 | — |
| macOS(`macos-26`) | `sysadminctl`, uid 502 | pass: 8파일 | pass: `PermissionError`, 경고 | — |
| Windows(`windows-2025`) | `net user`, 로그온 토큰(type 2) | pass: 8파일 | pass: `PermissionError`(게시한 계정의 임시 폴더). 경고 없음(ACL을 읽지 않음) | pass: `\\localhost\…`에 두 번 게시, 잠금 `held`, `rename_new`, 두 번째 계정이 8파일을 읽음 |

- 닫힌 대상은 POSIX에서 `0700` 폴더 아래, Windows에서 게시한 계정의 `%TEMP%` 아래다.
- Windows에서는 새 프로세스를 다른 계정으로 띄우지 않았다. 읽기 스크립트가 `LogonUserW`·`ImpersonateLoggedOnUser`로 그 계정의 토큰을 얻은 뒤
  파일을 연다.
- 같은 기기의 다른 계정이 읽는지만 본 것이다. 다른 기기의 독자가 브라우저로 여는 것은 #24에서 소유자가 확인한다.

## 도착 확인(`tools/channel_arrival_eval.py`, 설계 7절)

Chromium 156.0.8078.4·Firefox 157.0·WebKit 27.2(Playwright) × `file://`·HTTP 하위 경로 `/a/b/`에서 쟀다.

- **이벤트**: channel 폴더(`projects/<channel>/`)의 페이지가 버전의 파일을 불렀다. 대상은 `media="not all"`·`print` 스타일시트와 이미지다. 여섯
  조합 모두에서 파일이 있으면 `load`, 없으면 `error`였다. 스타일시트는 그 페이지에 적용되지 않았고, CSP `'self'`는 `file://`의 상위 폴더 파일도 받았다.
- **kit의 페이지**(두 build를 게시한 대상):

  | 경우 | 여섯 조합의 결과 |
  |---|---|
  | 최신 버전의 파일이 다 있음 | 모두 최신 버전으로 옮김, 0.014–0.079초 |
  | 최신 버전의 스타일시트가 없음 | 모두 옮기지 않고 안내를 보임 |
  | 스크립트를 끔 | 모두 `<noscript>` refresh로 옮김 |

## 측정(`tools/publish_eval.py`)

- **작은 프로젝트**(파일 9개, 다섯 번의 중앙값):

  | 경우 | 결과 | 시간 |
  |---|---|---|
  | 첫 게시 | `published`·전송 `done` | 0.037초 |
  | 같은 build 다시 | `published`·전송 `reused`·전환 `already` | 0.018초 |
  | 더 새 build | `published` | 0.036초 |
  | 더 오래된 build | `not_newer`·`reused` | 0.019초 |
  | 오래된 build로 되돌리기(`--switch-to`) | `published`·전송 `not_started`·전환 `done` | 0.019초 |

- **경계 중단**(여덟 경계 × 첫 게시·이후 게시, 16개): PR-2와 같다.
  - 모두 `interrupted` receipt가 남았다.
  - receipt의 포인터는 파일과 같았다.
  - 새 버전을 가리키는 포인터는 `channel_page` 경계에서만 나왔고, 그때는 최종 확인이 끝나 있었다.
  - 다시 실행하면 16개 모두 `published`였다.
- **두 프로세스가 동시에 게시**(각 20쌍):

  | 대상 잠금 | 결과 |
  |---|---|
  | 있음 | 40개 모두 exit 0·receipt, 최신은 늘 새 build(오래된 쪽 `not_newer` 8번), 최대 0.109초 |
  | 없음(주입) | 40개 모두 exit 0·receipt. 최신은 오래된 build 12번·새 build 8번(`superseded` 7번). 1번은 `latest.json`이 새 build, 페이지가 오래된 build로 갈렸다 |

  잠금 없는 쪽은 설계가 받아들인 한계다(동시 게시의 순서 비보장). 두 경우 모두 staging과 stderr는 0이었다.
- **M 규모**(#20 PR5의 M 사본, build 두 개):

  | 경우 | 내용 | 결과 | 시간 |
  |---|---|---|---|
  | 첫 build | read: 426파일, 12,839,471 B | `published` | 2.82초 |
  | 둘째 build | evidence: 1,612파일, 20,589,469 B | `published` | 5.12초 |
  | 첫 build 다시 | 대조 후 재사용 | `not_newer` | 1.45초 |
  | 첫 build로 되돌리기 | 그 자리 대조 | `published`·전송 `not_started` | 1.49초 |

  receipt는 3.1–3.3 KB다. 전체 도구 실행은 60.25초, 최대 메모리 310 MB였다.
- **exFAT**(macOS의 exFAT 디스크 이미지): PR-2와 같다. 두 번 모두 `failed`(`init`·`no_exclusive_rename`) receipt가 남았고, 빈 잠금 파일과 그
  AppleDouble만 남았다.

## 리뷰와 처분

일반 PR이므로 고정 커밋 `7823374`에서 독립 리뷰 1·Codex 리뷰 1을 했고 다시 리뷰하지 않았다(리뷰 예산).

| 리뷰 | 지적 | 처분 |
|---|---|---|
| 독립 구현 리뷰 | must-fix 2·minor 5·follow-up 6 | 모두 고침(`86cff5f`) |
| Codex 구현 리뷰(gpt-6.1-sol, xhigh) | must-fix 7·minor 3 | 여섯은 이미 고쳐짐(`d96776a`·`86cff5f`). 남은 넷(아래)은 `7286182` |

- 독립 리뷰의 must-fix 2는 다음과 같다.
  1. refusal의 단수 `path`가 receipt의 `failure.paths`에서 빠졌다(main 대비 회귀).
  2. 스무 개를 넘는 자원을 보지 않고 옮겼고, 문서에는 그 상한이 없었다. 상한을 없앴다.
- Codex의 남은 넷은 모두 문장 수정이다.
  1. 넘겨받은 channel을 다른 프로젝트의 버전으로 되돌린 뒤의 새 build는 `not_newer`다(설계 8절). D7에 적고 시험을 더했다.
  2. 저장 대상 파일을 읽지 못하면 그 폴더는 금지 값에 들지 않는다. D7에 적었다.
  3. `.gitignore` 검사의 범위를 설계 9.3에 적었다.
  4. 사용법에서 `--reassign-channel`을 `--build`에만 붙였다.
- Codex는 sandbox에서 pytest를 돌리지 못해 메모리 안의 재현으로 확인했다. 첫 실행은 표준 입력을 기다리며 멈춰 다시 실행했다.

## 미실행

- 다른 기기의 독자가 브라우저로 연 열람(#24, 소유자).
- 다른 기기의 SMB·NFS 마운트, 동기화 폴더(OneDrive, Dropbox)와 그 도착 지연.
- Safari 앱과 모바일 브라우저. CI의 브라우저 E2E는 Linux Chromium만 돌리고, Firefox·WebKit은 이 Mac에서 돌렸다.
- 측정 도구(`publish_eval`·`channel_arrival_eval`)의 Linux·Windows 실행.
- aelix 안의 `/analytics publish --switch-to`와 설치한 런처. `bin/analytics`는 이 checkout에서 실행했다.
- Windows ACL 관찰(`windows_acl: not_checked`). 그래서 Windows에는 `others_cannot_read` 경고가 없다.
- p50/p95를 위한 반복 측정. M 경우는 한 번씩 쟀다.
- 두 기기에서 동시에 게시하는 경우.
