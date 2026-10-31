# 지원 범위

작성: 2026-10-07 · 상태: 출시 전의 지원표 골격(B5 준비, [#24](https://github.com/handochan/aelix-analytics/issues/24)). 출시 판정이 아니다.

Aelix Analytics를 어떤 Python·운영체제·aelix·설치 경로에서 **실제로 설치하고 실행했는지**를 적는다.
지원 목표는 [ADR-0002](decisions/0002-python-support-and-platform-scope.md)대로 Linux·macOS·Windows이고, Python은 설치 환경(런처, aelix
확장, 확장이 로드하는 stdlib core)이 3.11 이상, 분석 프로젝트 환경이 3.12 이상이다([ADR-0004](decisions/0004-minimal-distribution-and-entry-points.md) 결정 3).
목표와 검증 결과를 한 칸에 합치지 않는다: 이 표에는 실제 설치·실행 증거가 있는 조합만 `verified`로 적는다(ADR-0002 결정 1,
[베타 품질 기준](BETA-QUALITY-GATES.md)의 Q1).
아래의 '설치'는 각 칸의 증거가 설치한 파일이다. 체크아웃에서 빌드한 wheel인지 릴리스 후보의 파일(`docs/releases/<번호>.sha256`)인지는
그 증거에 적는다. 게시한 번호는 [변경 이력](../CHANGELOG.md)에 있다.
무엇을 약속하고 무엇을 약속하지 않는지는 [현재 계약](CONTRACTS.md)이 기준이다. 이 문서는 그 F절이 인용하는 측정 기록과 main의 CI
실행 결과를 조합별로 모은 것이다.
보안 문제의 신고는 [SECURITY.md](../SECURITY.md)에 있다.

## 표시의 뜻

| 표시 | 뜻 |
|---|---|
| `verified` | 인용한 증거에서 그 조합으로 설치·실행한 시험이 통과했다. 증거는 main의 CI 실행, PR head의 CI 실행(실행 번호) 또는 [측정 기록](verification/)이고, 그 증거가 검사한 commit을 함께 적는다 |
| `expected` | 지원 목표 안이고 가까운 조합의 증거가 있지만, 그 조합으로는 실행하지 않았다. 통과로 세지 않는다 |
| `not run` | 실행한 기록이 없다 |
| 지원하지 않음 | 선언한 범위 밖이다 |

`verified`는 증거가 검사한 commit의 결과다. 그 뒤의 코드 변경은 다시 실행하기 전까지 그 칸을 넓히지 않는다.

## 증거

| 이름 | 무엇 | 검사한 코드 |
|---|---|---|
| CI-main | main push의 저장소 CI, [실행 37582037485](https://github.com/handochan/aelix-analytics/actions/runs/37582037485)(시도 1, 2026-10-07). `lint`와 `ubuntu-24.04`·`windows-2025`의 `platform`·`floor` 작업이 모두 성공했다 | `6c4c2cd`(#71의 병합). 그 뒤 main의 `384a168`(#73)은 문서만 바꿨고, #24의 B5 준비는 wheel의 메타데이터(`Author`·`Maintainer`, README에서 오는 설명)와 그 시험을 바꿨다. 둘 다 `src/`·`extension/`·`bin/`의 코드는 바꾸지 않았다 |
| CI-PR1 | [#24](https://github.com/handochan/aelix-analytics/issues/24) PR-1([#140](https://github.com/handochan/aelix-analytics/pull/140))의 CI, [실행 40990527927](https://github.com/handochan/aelix-analytics/actions/runs/40990527927)(시도 1, 2026-10-30). 필수 검사 일곱 개가 성공했다. 저장소 시험(설치 레그 포함): Linux 3,113 통과·112 건너뜀, Windows 2,916 통과·340 건너뜀, macOS 3,114 통과·111 건너뜀 | `cc8aa7b`(PR #140의 head, kit 0.31.0) |
| R-42 | [줄 끝 측정 기록](verification/2026-10-06-issue-42-line-endings.md): Linux·Windows CI, `macos-26`의 라벨 실행([37411847760](https://github.com/handochan/aelix-analytics/actions/runs/37411847760)), macOS 개발 기기 | `d23ddb8`(PR #62의 head, kit 0.4.0) |
| R-8w | [Windows 인벤토리 기록](verification/2026-10-02-issue-8-windows-inventory.md): 같은 세 플랫폼과 macOS 개발 기기의 3.13·3.14 | `4eb0955`(PR #35, kit 0.1.0) |

CI-main에서 확인한 것(이 실행의 artifact는 2027-01-05에 지워지므로 여기에 요약한다):

- 환경. Linux는 GitHub 호스트 이미지 `ubuntu24` 20260927.320.1(x64), Windows는 `win25-vs2026` 20260925.250.1(Windows Server 2025
  Datacenter, build 26100 x64, 관리자, Developer Mode 켬, LongPathsEnabled=1). uv 0.11.19. 설치 환경과 aelix 시험의 Python은 uv가 찾은
  CPython 3.11.15·3.12.3(Linux), 3.11.9·3.12.10(Windows)이고, `floor` 작업은 `actions/setup-python`의 3.11.16·3.11.9다. aelix는 PyPI의
  0.1.0b2(`aelix-ai`·`aelix-agent-core`·`aelix-coding-agent`도 0.1.0b2)다.
- 저장소 시험(배포본 end-to-end 포함, `AELIX_ANALYTICS_TEST_INSTALL=1`): Linux 1,305 통과·37 건너뜀, Windows 1,180 통과·170 건너뜀, 실패
  없음. 그 가운데 `tests/test_packaging.py`는 Linux에서 25개 모두, Windows에서 24개가 통과했고 1개(stand-in uv 스크립트를 쓰는 시험,
  Windows는 `.exe`만 시작함)를 건너뛰었다.
- 평가 하네스의 시험(저장소 시험 안): `tests/test_model_eval.py`는 Linux 56개 통과, Windows 48개 통과·8개 건너뜀(POSIX 신호 등),
  `tests/test_model_eval_scoring.py`는 두 OS에서 71개 통과다(둘을 합쳐 Linux 127개 통과, Windows 119개 통과·8개 건너뜀). 그 가운데
  `tests/test_model_eval.py`의 dry-run 시험은 uv로 aelix 0.1.0b2를 받아 `-e`로 체크아웃의 확장을 주고, 가짜 모델 서버로 과제 두 개를
  끝까지 돌렸다.
- aelix 0.1.0b2 통합(`tests/test_extension.py`·`tests/test_usage_report.py`): 두 OS에서 CPython 3.12·3.11 각각 151개 통과. 런처
  시험(`tests/test_launcher.py`, CPython 3.11): Linux 120 통과·1 건너뜀, Windows 62 통과·59 건너뜀(POSIX 전용 사례). 이 시험은 uv 대신
  호출을 기록하는 stand-in을 써서 kit을 실행하지 않는다. 플랫폼 스모크와 stdlib만의 스모크(3.11)는 두 OS에서 위반 0이다(Windows의
  선언된 한계는 [현재 계약 G](CONTRACTS.md#g-약속하지-않는-것)).

## 운영체제

| 운영체제 | 측정한 구성 | 상태 | 증거 |
|---|---|---|---|
| Linux | Ubuntu 24.04 x64(GitHub 호스트 러너) | `verified` | CI-main |
| Linux의 다른 배포판·arm64 | — | `not run` | |
| macOS | macOS 26.6.2 arm64(호스트 러너 `macos-26`, 개발 기기) | `verified`(`d23ddb8`). 그 뒤 main의 코드(#64·#66 등)는 macOS에서 실행하지 않았다 | R-42 |
| macOS의 다른 버전·Intel(x64) | — | `not run` | |
| Windows | Windows Server 2025 Datacenter x64 build 26100, 관리자, Developer Mode 켬, LongPathsEnabled=1(긴 경로 시험은 0으로도) | `verified`. 선언된 한계가 있다(아래 '알려진 한계') | CI-main, R-8w |
| Windows 클라이언트 판(같은 build의 Windows 11 24H2 포함), 표준 사용자, 기본 설정 | — | `not run` | [현재 계약 F](CONTRACTS.md#f-실행-환경과-지원-플랫폼) |

## Python

| Python | 설치 환경(런처·aelix 확장·stdlib core) | 분석 프로젝트 환경 |
|---|---|---|
| 3.10 이하 | 지원하지 않음(`requires-python = ">=3.11"`) | 지원하지 않음 |
| 3.11 | `verified`: Linux·Windows(CI-main), macOS(`d23ddb8`, R-42) | 지원하지 않음(`>=3.12`). 분석 라이브러리를 쓰지 않는 스크립트의 run과 stdlib 명령만 3.11에서 확인했다(CI-main) |
| 3.12 | `verified`: Linux·Windows(CI-main), macOS(`d23ddb8`, R-42) | `verified`: 새 프로젝트는 `.python-version`을 3.12로 두고, 설치본이 실제 uv로 그 환경을 만들어 run을 기록했다(CI-main, R-42) |
| 3.13, 3.14 | `expected`: macOS 개발 기기에서 저장소 시험(end-to-end 없이)만 `4eb0955`에서 통과(R-8w). 설치는 `not run` | `not run`(새 프로젝트는 3.12) |

## aelix

| aelix | 상태 | 증거 |
|---|---|---|
| 0.1.0b2 | `verified`: 체크아웃의 확장 파일을 aelix의 확장 로더로 로드해 도구·명령·hook을 부르는 통합 시험(3.12·3.11), 설치본 wheel의 결합(`aelix extension verify`가 `BOUND`)과 로드, 설치본 확장의 도구로 만든 프로젝트 | CI-main(Linux·Windows), R-42(macOS) |
| 0.1.0b2의 프로세스에 체크아웃의 확장을 줌(`aelix --mode json -p … -e <확장>`) | `verified`(Linux·Windows): 정해진 답을 돌려주는 가짜 모델 서버로 도구를 부르는 평가 하네스의 시험(`tests/test_model_eval.py`의 dry-run). macOS는 인용할 기록이 없어 `expected` | CI-main |
| 0.1.0b2보다 새 버전 | `not run`(2026-10-07에 PyPI에 없음) | |
| 사람이 쓰는 대화형 세션 | `not run`: 위 시험은 로더·도구를 직접 부르거나 가짜 모델로 돈다. Windows의 실제 aelix 세션도 실행하지 않았다 | [현재 계약 F·G](CONTRACTS.md#g-약속하지-않는-것) |
| 실제 모델과의 실행 | 판정 없음: 아래 '모델' | |

## 설치 경로

| 경로 | Linux | Windows | macOS | 증거(시험) |
|---|---|---|---|---|
| `uv tool install --python 3.12 <wheel>`(사람용 `analytics`) | `verified` | `verified` | `verified`(`d23ddb8`) | `test_the_readme_s_uv_tool_install_routes` |
| `uv tool install --python 3.12 aelix==0.1.0b2 --with <wheel>`(aelix와 함께) | `verified` | `verified` | `verified`(`d23ddb8`) | 같은 시험(`aelix extension verify`) |
| 새 가상 환경에 wheel 설치(3.11·3.12): 런처, aelix 결합·로드, 확장의 도구, aelix 없는 `analytics` | `verified` | `verified` | `verified`(`d23ddb8`) | `test_the_installed_*`, `test_aelix_binds_and_loads_the_installed_extension` |
| pip로 설치하고 제거(3.11) | `verified` | `verified` | `verified`(`d23ddb8`) | `test_pip_installs_and_uninstalls_the_wheel_without_leftovers` |
| sdist 설치(3.12) | `verified` | `verified` | `verified`(`d23ddb8`) | `test_the_sdist_installs_the_same_package` |
| 체크아웃의 symlink를 wheel 옆에 둠(README) | `verified` | `verified`(파일 링크, 관리자·Developer Mode) | `verified`(`d23ddb8`) | `test_a_checkout_symlink_beside_the_wheel_sets_up_one_copy` |
| 체크아웃의 `bin/analytics` | `expected`: 인자·환경·작업 디렉터리·종료 코드의 계약만 stand-in uv로 확인했고, 실제 uv와 kit을 `bin/analytics`로 실행하지 않았다 | 지원하지 않음(POSIX 셸 스크립트, 설치본 `analytics`를 씀) | `expected`(같음, `d23ddb8`) | `tests/test_launcher.py`의 동등성 시험(stand-in uv), `tests/test_platform.py`의 `--help` |
| 체크아웃의 확장 파일을 aelix에 줌(`aelix -e`) | `verified`(가짜 모델 서버의 비대화 실행) | `verified`(같음) | `expected`: 같은 파일을 aelix의 로더로 로드한 통합 시험만 인용할 수 있다(R-42) | `test_the_dry_run_goes_through_aelix_and_is_scored`, 위 'aelix' |
| `aelix extension install <wheel> --yes`(aelix의 uv tool 환경, pip 없음), 새 wheel을 다시 install, `aelix extension remove` | `verified`(CI-PR1) | `verified`(CI-PR1) | `verified`(CI-PR1) | `test_aelix_s_extension_commands_upgrade_a_wheel_and_remove_it` |
| `aelix extension update`로 경로 설치를 갱신 | 지원하지 않음: aelix 0.1.0b2는 처음 설치한 경로의 wheel을 다시 설치한다(이전 버전으로 돌아감). 새 wheel은 install로(README) | 같음 | 같음 | 같은 시험이 그 동작을 확인한다 |
| 이름으로 `aelix extension install aelix-analytics`·`update`·`remove`(PyPI 자리의 loopback 색인을 uv의 기본 색인으로) | `verified`(CI-PR1) | `verified`(CI-PR1) | `verified`(CI-PR1) | `test_aelix_installs_updates_and_removes_the_extension_by_name` |
| 이름으로 `uv tool install aelix-analytics`·`uv tool upgrade aelix-analytics`·`uv tool uninstall aelix-analytics`(같은 색인) | `verified`(CI-PR1) | `verified`(CI-PR1) | `verified`(CI-PR1) | `test_the_command_installs_upgrades_and_uninstalls_from_an_index` |
| 이름으로 `uv tool install aelix==0.1.0b2 --with aelix-analytics --with-executables-from aelix-analytics`·`uv tool upgrade aelix`·`uv tool uninstall aelix`(같은 색인을 먼저 묻는 색인으로) | `verified`(CI-PR1) | `verified`(CI-PR1) | `verified`(CI-PR1) | `test_aelix_takes_the_extension_and_its_command_from_an_index` |
| `--with-executables-from aelix-analytics`를 wheel 경로로 | `expected`: 위의 이름 설치와 같은 uv 경로이고 wheel 경로로는 실행하지 않았다 | `expected` | `expected` | |
| 이전 버전 위의 업그레이드: kit 0.25.0 wheel(`660beb63…`, Q17에서 쓴 것. 그 commit을 다시 빌드하면 Linux·macOS에서는 같은 바이트, Windows에서는 zip 헤더만 다르고 멤버와 내용이 같다) → 이 버전. 같은 `uv tool install` 줄을 새 wheel로, 위의 색인과 aelix 경로. 이전 버전이 만든 프로젝트를 이어 씀 | `verified`(CI-PR1) | `verified`(CI-PR1) | `verified`(CI-PR1) | `test_a_newer_wheel_file_replaces_the_earlier_one`, 위의 네 시험 |
| 실제 PyPI에서 이름으로 설치 | `not run`(이 표를 채울 때 게시 전) | `not run` | `not run` | |

macOS 열에 `d23ddb8`로 적은 `verified`는 그 commit의 결과다. 그때는 macOS를 라벨·수동·주간 실행에서만 돌렸고, 2026-10-21(#22)부터는
세 OS가 모든 PR과 main push에서 돈다([기여 가이드](../CONTRIBUTING.md#저장소-ci)). CI-PR1로 적은 칸은 그 실행의 결과다.

## 모델

검증한 모델은 아직 없다. 주 검증 대상의 설정 — 로컬 Qwen3.8 27B와 프론티어 모델 — 은 #40이 고정했고, 그 설정으로 측정한 결과는
#24의 출시 리허설(B6)이 낸다([베타 품질 기준 §1](BETA-QUALITY-GATES.md#1-출시할-약속)). 설정을 고정한 것은 검증 결과가 아니다.
[PR #71](https://github.com/handochan/aelix-analytics/pull/71)에서 두 모델이 평가 하네스로 과제 하나(T06)를 개발 기기에서 끝까지 실행한
것은 하네스의 동작 확인이며, 이 지원표의 증거나 모델의 판정이 아니다.

## 알려진 한계

지원표의 `verified`는 아래 한계를 없애지 않는다. 각 항목의 측정과 미실행은 [현재 계약 G](CONTRACTS.md#g-약속하지-않는-것)에 있다.

- **Windows의 한계**([#38](https://github.com/handochan/aelix-analytics/issues/38)): 긴 경로(MAX_PATH를 넘는 프로젝트 루트,
  LongPathsEnabled=0에서 잃는 출력 기록), 콘솔의 Ctrl+Break와 창 닫기, 확장이 넘기지 않는 환경 변수 등. 베타까지 고치지 않는다
  ([플랫폼 보강 동결](BETA-QUALITY-GATES.md#베타까지의-플랫폼-보강)). 사용자 안내는 [README](../README.md#windows에서-쓸-때)에 있다.
- **경로 정체성**([#36](https://github.com/handochan/aelix-analytics/issues/36)): 프로젝트 안의 디렉터리 링크 아래에서 빠지는 출력,
  대소문자·유니코드 정규화가 다른 경로, 다른 OS가 기록한 절대 경로(G의 '프로젝트 안의 디렉터리 링크', '대소문자와 유니코드 정규화',
  '관계의 읽기'). 기록 손실·계보 단절 항목은 #13 전에 고치기로 했다(동결의 예외). 지금의 동작은 G의 서술이다.
- **줄 끝**([#42](https://github.com/handochan/aelix-analytics/issues/42), [ADR-0007](decisions/0007-line-endings.md)): kit 0.4.0 전에
  Windows에서 쓴 기록은 CRLF로 남고, 사용자 파일과 스크립트가 직접 쓴 바이트는 그대로이며, `line_endings_only`가 알아보는 범위가
  정해져 있다(G의 '줄 끝').
- **실행하지 않은 것**: 실제 문서 열기 프로그램, 설치본의 `analytics demo`, 사람이 누른 Windows 콘솔 키, Windows의 실제 aelix 세션
  ([현재 계약 F·G](CONTRACTS.md#f-실행-환경과-지원-플랫폼)).
- **샌드박스가 아니다**: 스크립트는 사용자 권한으로 돌고 aelix는 확장 도구 실행 전에 묻지 않는다([README 한계](../README.md#한계)).

## 베타에서 지원을 정하는 방법

- 이 표는 commit마다 낡는다. 출시 후보에서는 출시할 그 artifact(wheel·sdist의 sha256)로 다시 채우고, 칸마다 그 commit의 실행을
  증거로 적는다([B5·B6](BETA-QUALITY-GATES.md#5-구현-순서)). 그 전의 `verified`를 출시 후보의 결과로 옮겨 적지 않는다.
- B5에서 남은 확인: 출시 후보로 돌린 설치·제거·업그레이드(위 표의 행), Aelix 연동과 사람용 단독
  설치를 구별한 깨끗한 환경의 E2E([Q1·Q17-a](BETA-QUALITY-GATES.md#2-첫-베타의-필수-게이트)). 실제 PyPI에서의 설치는 게시한 뒤에만 할 수 있다.
- B6의 출시 리허설이 Q1–Q17 결과표를 채운다. 필수 게이트의 실패·미실행, 설치물 누락은 출시 보류 사유다
  ([§7](BETA-QUALITY-GATES.md#7-기여출시-기록의-형식)).
- 베타는 지원 목표(세 플랫폼)를 공개하되, 지원표에는 출시 후보로 실행한 칸만 `verified`로 적고 나머지는 `expected`·`not run`
  그대로 공개한다(ADR-0002 결정 1).
- 베타 전에는 wheel을 외부에 게시하지 않는다(2026-10-03 소유자 결정, #24). 배포 이름은 `aelix-analytics`(2026-10-17 소유자 결정)이고, 게시는 PyPI와
  GitHub Release에 측정한 그 파일을 올린다([릴리스 절차](../CONTRIBUTING.md#릴리스-절차), 2026-10-30 소유자 결정).
