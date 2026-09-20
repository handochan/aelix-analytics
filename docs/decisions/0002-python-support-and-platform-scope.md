# ADR-0002: Python 지원 범위와 플랫폼 경계

Date: 2026-09-21

Status: Proposed — 지원 플랫폼은 2026-09-21 사용자가 Linux·macOS·Windows로 확정했다. Python 하한과
패키징 경계는 [Issue #5](https://github.com/handochan/aelix-analytics/issues/5)의 결정 초안이며 아직 코드에 적용되지 않았다.

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

문서의 현재 서술은 macOS/Linux다(`README.md:79`, `docs/BETA-QUALITY-GATES.md:20`). 지원 버전·플랫폼은
[기여 기준](../../CONTRIBUTING.md)이 첫 공개 전 확정 대상으로 남겨 둔 항목이다.
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

즉 진입점의 실제 하한은 3.11이며 선언이 한 단계 높다. 그리고 **Windows에서는 현재 확장이 로드되지 않는다.**

## Windows에서 동작하지 않는 지점

| 위치 | 사용 | Windows |
|---|---|---|
| `kit/labkit/core.py:12` | 가드 없는 최상위 `import fcntl` | 모듈 없음 → import 실패 |
| `kit/labkit/core.py:239,243,251,255` | `fcntl.flock` — journal·context 잠금, run 직렬화 | 없음 |
| `kit/labkit/report.py:404,408,412` | `fcntl.flock` — report 빌드 잠금 | 없음 |
| `kit/labkit/runner.py:455,492` | `signal.SIGALRM`으로 run 타임아웃 | `SIGALRM` 없음 |
| `extension/aelix_lab.py:163` | `os.killpg(..., signal.SIGKILL)` | 둘 다 없음 |
| `extension/aelix_lab.py:172,243` | `start_new_session=True` | POSIX 전용 |
| `bin/lab` | `#!/bin/sh`, `readlink`·`pwd -P`·`sed`·`open` | 대응 진입점 없음 |

`core.py`는 "aelix 프로세스 안에서 경로로 로드되므로 stdlib만 사용한다"고 스스로 밝히고 있다.
그 파일의 첫 import가 POSIX 전용이라는 점이 Windows 지원의 첫 번째 장애물이다.

## 결정 제안

1. **지원 플랫폼은 Linux·macOS·Windows로 한다**(사용자 결정). 세 플랫폼을 **지원 목표**로 공개하되,
   문서의 지원표에는 실제 설치·실행 증거가 있는 범위만 "검증됨"으로 적는다. 목표와 검증 완료를 한 칸에 합치지 않는다.
2. **진입점(Aelix host 어댑터·사람용 CLI·SDK)의 하한을 `>=3.11`로 낮춘다.** Aelix의 하한과 같아지므로
   host가 3.11로 실행돼도 확장이 로드된다. 근거는 위 측정 결과다.
3. **분석 프로젝트 환경은 `>=3.12`를 유지한다.** 이 환경은 프로젝트마다 uv가 새로 만들고 host와 공유하지 않으므로,
   더 높은 하한을 두어도 확장 로드를 막지 않는다.
4. **기본 설치는 Aelix를 필수 의존성으로 두지 않는다.** 사람이 CLI·SDK만으로 분석·기록을 완결할 수 있어야 하고,
   Aelix 연동은 선택적 추가 설치로 둔다. 분석 라이브러리는 진입점에 넣지 않고 프로젝트 환경에만 설치한다.
5. **POSIX 전용 호출을 이식 가능한 경계 뒤로 옮긴다.** 파일 잠금·실행 타임아웃·자식 프로세스 종료는
   각각 하나의 내부 경계로 모으고 플랫폼별 구현을 주입한다. 호출부에 `if sys.platform` 분기를 흩지 않는다.
6. **Windows 진입점을 별도로 제공한다.** `bin/lab`은 POSIX 셸 래퍼로 두고, Windows에는 같은 계약의 진입점을 따로 만든다.
7. **배포 형태를 확정한다.** 현재 `package = false`이므로 설치 가능한 배포본이 없다. 최소 wheel의 내용과
   `bin/lab`·확장 파일의 설치 위치는 [#8](https://github.com/handochan/aelix-analytics/issues/8)에서 구현과 함께 고정한다.

## 대안과 선택 이유

| 대안 | 검토 결과 |
|---|---|
| 진입점도 `>=3.12` 유지 | 선언이 실제 요구보다 높고, Aelix가 3.11로 실행되는 설치에서 확장이 로드되지 않는다 |
| 분석 환경까지 `>=3.11`로 낮춤 | 이득이 없다. 프로젝트 환경은 격리돼 있어 host 하한을 제약하지 않는다 |
| Windows를 지원표에서 제외 | 사용자 결정과 어긋난다 |
| Windows를 지원 목표로 적고 현재 상태를 검증됨으로 표기 | 실제로는 import조차 실패한다. 저장소의 증거 기준을 위반한다 |
| 호출부마다 `sys.platform` 분기 | 잠금·타임아웃·종료 의미가 플랫폼별로 갈라지고 시험이 어려워진다 |

## 결과와 비용

- Windows 지원은 선언 변경으로 끝나지 않는다. 위 표의 7개 지점이 실제 이식 작업이며 `#8`의 범위에 들어간다.
- 진입점 하한을 낮추면 검증 대상 조합이 늘어난다. CI 매트릭스는 [#22](https://github.com/handochan/aelix-analytics/issues/22)에서 다룬다.
- 파일 잠금의 의미는 플랫폼마다 다르다. 동시 실행 시험을 세 플랫폼에서 각각 수행해야 하며,
  한 플랫폼의 통과를 다른 플랫폼의 결과로 옮기지 않는다.
- 이 ADR은 채택 전까지 현재 계약이 아니다. [현재 계약](../CONTRACTS.md)은 채택·구현 시점에 함께 갱신한다.

## 채택 시 확인할 것

Issue #5 완료 조건 3의 A0 검증 계획이다. 각 항목은 실행 명령·환경·결과를 증거로 남긴다.

1. 진입점 하한: Python 3.11과 3.12 각각에서 `python -m labkit`의 init/import/query/run/note/report/status/context를 실행한다.
2. 최소 wheel: 새 환경에 설치해 확장 로드와 사람용 CLI 실행을 확인하고, 분석 라이브러리가 진입점 환경에 들어오지 않음을 확인한다.
3. 격리: 프로젝트 uv 환경이 진입점 환경과 분리돼 생성되고, Aelix 미설치 상태에서도 분석·기록이 완결되는지 확인한다.
4. 플랫폼: Linux·macOS·Windows에서 각각 1~3을 수행한다. 동시 run·타임아웃·취소를 플랫폼별로 따로 시험한다.
5. 미검증 범위를 지원표에 그대로 남긴다. 실행하지 않은 조합은 `not_run`으로 적는다.

세 플랫폼 전체 통과 전까지 이 ADR의 지원 목표를 "검증된 지원 범위"로 안내하지 않는다.
