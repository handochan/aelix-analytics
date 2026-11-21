# Issue #24 — 게시한 0.32.1을 PyPI에서 이름으로 설치 (2026-11-22)

[Issue #24](https://github.com/handochan/aelix-analytics/issues/24)의 [릴리스 절차](../../CONTRIBUTING.md#릴리스-절차) 6단계 기록이다. 게시한
`aelix-analytics` 0.32.1을 실제 PyPI에서 이름으로 설치했다. 수치·명령의 정본은 [JSON](2026-11-22-issue-24-pypi-install.json)이다.
설치 환경은 이 기기의 임시 디렉터리에만 두고 커밋하지 않았다.

## 게시

- tag `v0.32.1`은 main `c99e5a4`에 붙였다(2026-11-21 17:32Z, 소유자 승인).
- release run [44930597708](https://github.com/handochan/aelix-analytics/actions/runs/44930597708)이 모두 성공했다.
  - `build`: 다시 빌드한 파일이 `docs/releases/0.32.1.sha256`과 같은지 대조했다.
  - `verify`: 세 OS에서 설치를 확인했다.
  - `publish · PyPI`: 소유자가 `pypi` 환경을 승인한 뒤 올렸다(17:37:58Z). `tools/release_check.py pypi`가 PyPI의 두 파일이 그 sha256인지 확인했다.
  - `publish · GitHub Release`: 같은 파일과 `SHA256SUMS`를 붙였다.
- PyPI의 JSON API가 보이는 파일과 sha256은 후보 기록과 같다. wheel은 `878741132f704146…`, sdist는 `2f6f14863467…`다.
  GitHub Release의 두 파일도 같은 digest다.

## 이름으로 설치

환경은 macOS 26.6.2 arm64(개발 기기)와 uv 0.11.19다. 새 HOME, uv의 tool 디렉터리, XDG 디렉터리, aelix 설정 디렉터리, kit의 신뢰 목록을
모두 임시 디렉터리로 두어, 기기 사용자의 aelix·uv 도구를 건드리지 않았다. 색인은 uv의 기본(PyPI)이다.

| 경로 | 결과 |
|---|---|
| `uv tool install --python 3.12 aelix-analytics`([README](../../README.md#quick-start)의 첫 줄) | 설치된 배포본 0.32.1. `analytics demo`가 run 4개(다시 실행 비교 `match`)와 Workspace를 만들었다. `uv tool uninstall aelix-analytics`로 제거 |
| `uv tool install --python 3.12 aelix==0.1.0b2 --with aelix-analytics --with-executables-from aelix-analytics`(README의 둘째 줄) | `aelix`·`analytics`가 생김. `aelix extension verify`: `BOUND aelix-analytics [aelix-analytics 0.32.1]`, all 1 endpoint(s) BOUND. `uv tool uninstall aelix`로 제거 |
| aelix 0.1.0b2만 설치한 뒤 `aelix extension install aelix-analytics==0.32.1 --yes` | 색인에서 설치(`direct_url.json` 없음). `aelix extension verify` BOUND. `aelix extension remove aelix-analytics --yes`로 제거되고 배포본이 남지 않음 |

[aelix marketplace](https://github.com/handochan/aelix-marketplace)의 제출 게이트(`scripts/verify_candidates.py`)도 이 소스(`aelix-analytics==0.32.1`)를
설치해 `BOUND`로 판정했다(aelix-ai `0436193` 소스, [aelix-marketplace#12](https://github.com/handochan/aelix-marketplace/pull/12)).

## 실행하지 않은 것

- Linux·Windows에서 PyPI의 이름으로 설치. 같은 경로를 PyPI 자리의 loopback 색인으로 세 OS에서 잰 것은 REL-0.32.1(release run의 `verify`)이다.
- `uv tool upgrade`로 앞선 게시본에서 올리기. 앞선 게시본이 없다.
