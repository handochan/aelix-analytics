# ADR-0002: Python 지원 범위와 플랫폼 경계

Date: 2026-09-21

Status: Accepted (2026-09-21) — [Issue #5](https://github.com/handochan/aelix-analytics/issues/5)에서 사용자가 채택했다.
지원 플랫폼은 Linux·macOS·Windows다. 채택은 결정의 확정이며 구현 완료가 아니다. 구현과 검증은
[#8](https://github.com/handochan/aelix-analytics/issues/8)에서 수행한다: 결정 5는 첫 PR([ADR-0003](0003-portable-boundaries-and-in-process-kit-loading.md)),
결정 2·4·6·7은 두 번째 PR([ADR-0004](0004-minimal-distribution-and-entry-points.md))에서 적용했다. ADR-0004는 결정 6을
이렇게 구현했다: `bin/lab`은 체크아웃의 POSIX 래퍼로 두고, 같은 계약의 `lab` 런처는 Windows를 포함한 모든 플랫폼에서
설치본의 명령이 된다. 아래 "구현에서 확인할 것" 1을 어떻게 읽었는지도 ADR-0004에 있다.
현재 코드가 실제로 하는 것은 [현재 계약](../CONTRACTS.md)의 "실행 환경과 지원 플랫폼" 절에 있다.
채택 후 독립 리뷰에서 근거 과장과 인벤토리 누락을 지적받아 측정 근거·장애 범주·대안·결정 5·6을 개정했다. 결정의 방향은 바뀌지 않았다.

## 배경

현재 구현에는 Python이 실행되는 맥락이 네 개 있고 각각 제약이 다르다.

| 맥락 | 무엇이 실행되는가 | 설치 위치 |
|---|---|---|
| Aelix host 어댑터 | `extension/aelix_lab.py`가 `kit/labkit/core.py`를 경로로 로드한다(`spec_from_file_location`) | Aelix 프로세스 안 |
| 사람용 CLI | `python -m labkit`, `bin/lab` | 사용자의 Python 또는 pack 환경 |
| 분석 worker | `runner`·`data`·`report`가 분석 라이브러리를 사용한다 | 프로젝트별 uv 환경 |
| 개발 검증 | 저장소 스위트 | 저장소 `uv.lock` 환경 |

선언은 세 곳 모두 `requires-python = ">=3.12"`이고(`pyproject.toml:5`, `kit/template/pyproject.toml:7`, `uv.lock:3`),
Aelix는 `>=3.11`이다. Issue #5는 "진입점 3.11+/분석 3.12+"를 검증 전 권장안으로 적어 두었다.
`pyproject.toml`의 `package = false`는 현재 이 pack이 어떤 wheel로도 배포되지 않는다는 뜻이다.

이 결정 직전 `9315934`에서 문서의 서술은 macOS/Linux였고(`README.md:79`, `docs/BETA-QUALITY-GATES.md:20`),
지원 버전·플랫폼은 [기여 기준](../../CONTRIBUTING.md)이 첫 공개 전 확정 대상으로 남겨 둔 항목이었다.
이 결정과 같은 변경에서 세 문서를 함께 고쳤으므로 위 줄 번호는 `9315934` 기준이다.
[회귀 기준](../BASELINE-VERIFICATION.md)이 고정한 실제 검증 범위는 macOS 26.6.2 arm64 / Python 3.12.13 하나뿐이다.

## 측정 근거

이 결정의 근거는 추정이 아니라 이번 조사에서 실행한 결과다.

| 확인 | 명령·방법 | 결과 |
|---|---|---|
| 3.11 구문 호환성 | `uv run --python 3.11 --no-project python -m compileall -q kit extension tools tests` | exit 0 |
| 3.11 진입점 동작 | `PYTHONPATH=kit` 로 `import labkit.core` | Python 3.11.15에서 성공, `SCHEMA_RUN=lab.run/1` |
| 3.12 전용 문법 | PEP 695 type parameter, `itertools.batched` 검색 | 사용처 없음 |
| 실제 stdlib 하한 | `tomllib`(`core.py:17`), `datetime.UTC`(`tools/check_docs.py:20`) | 둘 다 3.11+ |
| Windows 모듈 가용성 | `fcntl` import를 차단한 상태에서 모듈 import | `labkit.core`·`labkit.report` 모두 `ModuleNotFoundError: No module named 'fcntl'` |
| 고정된 분석 의존성의 하한 | `importlib.metadata`로 `Requires-Python` 조회 | numpy 2.5.3·contourpy 1.4.0은 `>=3.12`, matplotlib 3.11.2는 `>=3.11`, polars·duckdb·pyarrow는 `>=3.10` |
| 3.11에서 전체 스위트 | **실행하지 않음** — 저장소 `uv.lock`이 `>=3.12`라 잠긴 환경을 3.11로 만들 수 없다 | `not_run` |
| 3.11에서 CLI·SDK·확장 로더 | **실행하지 않음** | `not_run` |

이 측정은 **진입점을 3.11로 두는 것이 가능함을 뒷받침하지만, 실제 하한이 3.11임을 입증하지는 않는다.**
`compileall`은 구문만 검사하고, `import labkit.core` 하나는 CLI 명령·SDK 동작·확장 로더·의존성 설치를 실행하지 않는다.
PEP 695와 `itertools.batched`가 없다는 것도 전수 호환성 검사가 아니다. 고정된 분석 의존성 중 numpy와 contourpy가
`>=3.12`이므로 현재 `uv.lock` 그대로는 3.11 환경을 만들 수 없다. 하한 확정은 구현 단계의 실제 3.11 실행으로 판정한다.

확정된 사실은 하나다. **Windows에서는 현재 확장이 로드되지 않는다.**

## Windows에서 동작하지 않는 지점

확장 로드가 실패하는 지점은 확정적이다. `kit/labkit/core.py:12`가 가드 없는 최상위 `import fcntl`이고,
확장은 이 파일을 aelix 프로세스 안에서 경로로 로드한다(`extension/aelix_lab.py:43`). `fcntl`을 차단하면
`labkit.core`와 `labkit.report`가 모두 `ModuleNotFoundError`로 실패한다. `core.py`는 "aelix 프로세스 안에서
로드되므로 stdlib만 사용한다"고 스스로 밝히는 파일인데, 그 stdlib 선택이 POSIX 전용이다.

확인된 장애 범주는 최소한 다음과 같다. 각 범주에 여러 호출 지점이 있다.

| 범주 | 예 |
|---|---|
| 파일 잠금 | `core.py`의 journal·run 잠금과 `report.py`의 빌드 잠금 (`fcntl.flock`) |
| 실행 타임아웃 | `runner.py`의 `signal.SIGALRM`과 `signal.setitimer(ITIMER_REAL, …)` |
| 프로세스 신호·종료 | `aelix_lab.py`의 `os.killpg(…, SIGKILL)`는 Windows에 없다. `os.kill`은 있으나 의미가 달라 `SIGINT` 전달(`aelix_lab.py`)이 강제 종료가 되고 `sig=0` 생존 확인(`core.py`)이 `CTRL_C_EVENT`가 된다 |
| 자식 프로세스 생성 | `aelix_lab.py`의 `start_new_session`, 그리고 재구성한 자식 환경에 `SystemRoot`·`TEMP` 등이 없는 점 |
| 파일 교체와 공유 | 열린 핸들이 있는 대상을 `os.replace`로 교체하는 기록·보고서 경로 |
| 파일명 | `con`·`nul`·`aux` 같은 예약 장치명이 데이터셋·차트·스크립트 파일명이 될 수 있는 점 |
| 텍스트 인코딩 | 비 ASCII JSON 출력과 locale에 의존하는 파이프·파일 읽기 |
| 경로 표현 | 보고서의 외부 입력 링크가 역슬래시를 URL 인코딩하는 점 |
| 사람용 진입점 | `bin/lab`이 POSIX 셸 스크립트인 점 |
| 기존 회귀 시험 | `chmod(…, 0)`처럼 POSIX 권한 의미를 가정하는 시험 |

**이 표는 완전한 인벤토리가 아니다.** 정적 검사로는 symlink·긴 경로·대소문자 구분 같은 항목을 확정할 수 없고,
실제 Windows 실행 전까지 미검증으로 남는다. 완전한 목록과 이식 범위는 구현 Issue
[#8](https://github.com/handochan/aelix-analytics/issues/8)에서 확정한다. 이 ADR은 그 목록을 고정하지 않는다.

## 결정

1. **지원 플랫폼은 Linux·macOS·Windows로 한다**(사용자 결정). 세 플랫폼을 **지원 목표**로 공개하되,
   문서의 지원표에는 실제 설치·실행 증거가 있는 범위만 "검증됨"으로 적는다. 목표와 검증 완료를 한 칸에 합치지 않는다.
2. **진입점(Aelix host 어댑터·사람용 CLI·SDK)의 하한을 `>=3.11`로 낮춘다.** Aelix의 하한과 같아지므로
   host가 3.11로 실행돼도 확장이 로드된다. 위 측정은 이 선택이 가능함을 뒷받침할 뿐이며, 하한 확정은
   구현 단계의 실제 3.11 실행으로 판정한다.
3. **분석 프로젝트 환경은 `>=3.12`를 유지한다.** 이 환경은 프로젝트마다 uv가 새로 만들고 host와 공유하지 않으므로,
   더 높은 하한을 두어도 확장 로드를 막지 않는다.
4. **기본 설치는 Aelix를 필수 의존성으로 두지 않는다.** 사람이 CLI·SDK만으로 분석·기록을 완결할 수 있어야 하고,
   Aelix 연동은 선택적 추가 설치로 둔다. 분석 라이브러리는 진입점에 넣지 않고 프로젝트 환경에만 설치한다.
5. **새 코드는 처음부터 세 플랫폼을 가정하고 작성한다.** POSIX 전용 API를 새로 도입하지 않는다. 파일 잠금·실행
   타임아웃·자식 프로세스 생성과 종료·파일 교체·파일명·경로 표현·텍스트 인코딩은 **처음부터** 이식 가능한 내부
   경계로 정의하고 플랫폼별 구현을 주입한다. 호출부에 `if sys.platform` 분기를 흩지 않는다. 기존 POSIX 전용
   호출은 그 경계를 만들 때 함께 옮긴다. 이식을 나중 단계로 미루지 않는다.
6. **사람용 진입점은 플랫폼별로 제공한다.** `bin/lab`은 POSIX 셸 래퍼로 두고, Windows에는 같은 계약의 진입점을
   따로 만든다. 진입점이 갈라져도 명령 이름·종료 코드·JSON 응답 계약은 하나로 유지한다.
7. **배포 형태를 확정한다.** 현재 `package = false`이므로 설치 가능한 배포본이 없다. 최소 wheel의 내용과
   `bin/lab`·확장 파일의 설치 위치는 [#8](https://github.com/handochan/aelix-analytics/issues/8)에서 구현과 함께 고정한다.

## 대안과 선택 이유

| 대안 | 검토 결과 |
|---|---|
| 진입점도 `>=3.12` 유지 | 선언이 실제 요구보다 높을 가능성이 크지만 이번 측정으로 확정되지는 않았다. 또한 현재 확장은 `spec_from_file_location`으로 `core.py`를 직접 로드하므로(`aelix_lab.py:37-46`) 이 선언 자체가 3.11 host의 로드를 막지는 않는다. 제약이 되는 것은 앞으로 만들 wheel의 설치 조건이다 |
| 분석 환경까지 `>=3.11`로 낮춤 | 이득이 없다. 프로젝트 환경은 격리돼 있어 host 하한을 제약하지 않는다 |
| Windows를 지원표에서 제외 | 사용자 결정과 어긋난다 |
| Windows를 지원 목표로 적고 현재 상태를 검증됨으로 표기 | 실제로는 import조차 실패한다. 저장소의 증거 기준을 위반한다 |
| 호출부마다 `sys.platform` 분기 | 잠금·타임아웃·종료 의미가 플랫폼별로 갈라지고 시험이 어려워진다 |
| POSIX로 먼저 만들고 나중에 이식 | 공통 경계에 POSIX 가정이 박힌 뒤 다시 뜯게 된다. 사용자 결정에 따라 처음부터 세 플랫폼을 가정한다 |
| 이 ADR에서 Windows 인벤토리를 확정 | 정적 검사로는 완전해지지 않는다. 독립 리뷰가 이 표의 누락을 실제로 찾아냈다. 목록은 실행 증거와 함께 #8에서 확정한다 |

## 결과와 비용

- Windows 지원은 선언 변경으로 끝나지 않는다. 위 범주 전부가 실제 구현 작업이며, 완전한 인벤토리 확정을 포함해 `#8`의 범위에 들어간다.
- 처음부터 세 플랫폼을 가정하면 `#8`의 경계 설계가 커진다. 대신 POSIX 가정이 박힌 경계를 나중에 다시 뜯는 비용을 피한다.
- 진입점 하한을 낮추면 검증 대상 조합이 늘어난다. CI 매트릭스는 [#22](https://github.com/handochan/aelix-analytics/issues/22)에서 다룬다.
- 파일 잠금의 의미는 플랫폼마다 다르다. 동시 실행 시험을 세 플랫폼에서 각각 수행해야 하며,
  한 플랫폼의 통과를 다른 플랫폼의 결과로 옮기지 않는다.
- 채택은 구현이 아니다. 어느 결정을 적용했는지는 위의 상태에 적는다. [현재 계약](../CONTRACTS.md)의
  "실행 환경과 지원 플랫폼" 절이 현재 코드를 기술하며, 구현이 진행되는 대로 그 절을 함께 갱신한다.

## 구현에서 확인할 것

Issue #5 완료 조건 3의 A0 검증 계획이다. 채택되었으므로 #8의 수용 조건에 포함한다. 각 항목은 실행 명령·환경·결과를 증거로 남긴다.

1. 진입점 하한: Python 3.11과 3.12 각각에서 `python -m labkit`의 init/import/query/run/note/report/status/context를 실행한다.
2. 최소 wheel: 새 환경에 설치해 확장 로드와 사람용 CLI 실행을 확인하고, 분석 라이브러리가 진입점 환경에 들어오지 않음을 확인한다.
3. 격리: 프로젝트 uv 환경이 진입점 환경과 분리돼 생성되고, Aelix 미설치 상태에서도 분석·기록이 완결되는지 확인한다.
4. 플랫폼: Linux·macOS·Windows에서 각각 1~3을 수행한다. 동시 run·타임아웃·취소를 플랫폼별로 따로 시험한다.
5. 기존 회귀 시험의 POSIX 가정을 함께 정리한다. `chmod(…, 0)`으로 읽기 불가를 만드는 시험처럼 런타임 이식만으로는
   통과하지 않는 사례가 있다.
6. 완전한 Windows 인벤토리를 실행 증거와 함께 확정한다. symlink·긴 경로·대소문자 구분은 실제 실행으로 판정한다.
7. 미검증 범위를 지원표에 그대로 남긴다. 실행하지 않은 조합은 `not_run`으로 적는다.

세 플랫폼 전체 통과 전까지 이 ADR의 지원 목표를 "검증된 지원 범위"로 안내하지 않는다.
