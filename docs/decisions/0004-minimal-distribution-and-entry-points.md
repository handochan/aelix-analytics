# ADR-0004: 최소 배포본 — wheel 구성, 설치 자원 조회, 사람용 진입점

Date: 2026-09-30

Status: Accepted (2026-09-30) — [ADR-0002](0002-python-support-and-platform-scope.md) 결정 2·4·6·7의 구현 방식을
정하는 결정이며 [Issue #8](https://github.com/handochan/aelix-analytics/issues/8)의 두 번째 PR에서 적용했다.
ADR-0003과 같이 새 제품 결정이 아니라 승인된 작업 안의 구조 선택이므로, 반대 의견은 해당 PR 리뷰에서 다룬다.
현재 코드가 실제로 하는 것은 [현재 계약](../CONTRACTS.md)의 A·D·F·G절에 있다.

## 배경

ADR-0002는 진입점의 하한을 `>=3.11`로 낮추고(결정 2), Aelix 없이 CLI·SDK만으로 분석을 완결하게 하며(결정 4),
사람용 진입점을 플랫폼별로 두고(결정 6), 최소 wheel의 내용과 `bin/lab`·확장 파일의 설치 위치를 #8에서 정하기로 했다
(결정 7). 이 PR 전까지 저장소는 `package = false`라 설치 가능한 배포본이 없었고, kit의 자원은 체크아웃 안의 상대
위치로 찾았다(`__main__`의 `parent.parent / "template"`, 확장의 `parents[1] / "kit"`).

이 결정을 좁힌 사실은 조사에서 실행으로 확인했다.

- aelix 0.1.0b2는 설치된 확장을 `aelix.extensions` entry point(`module:callable`)로 찾고 그 모듈을 평범하게
  import한다. manifest `aelix-plugin.toml`은 배포본의 RECORD가 entry 모듈의 경로 위 package 안에 그 파일을 둘
  때만 결합된다. editable 설치는 결합되지 않는다.
- 확장이 aelix 프로세스 안에서 `core.py`를 로드하는 합성 package(ADR-0003)에서는 `importlib.resources.files()`가
  동작하지 않는다(`ValueError`, 이어서 `ModuleNotFoundError`). 파일 경로로 로드된 모듈에서도 동작하지 않는다.
- 자식 프로세스는 kit을 최상위 `labkit`으로, 정확히 kit 디렉터리 하나를 PYTHONPATH에 두고 실행해야 한다. 다른
  이름으로 실행하면 스크립트가 import한 SDK가 실행 중인 run을 보지 못한다. site-packages 전체를 PYTHONPATH에 두는
  것은 [설계](../DESIGN-CONTINUITY-AND-REUSE.md) 8절이 금한다.
- uv의 build backend는 파일을 옮기지 않고는 `kit/`와 `extension/`을 한 package로 묶지 못한다. hatchling은 옮기지
  않고 묶으며 `.gitignore`를 따르고 재현 가능하게 만든다.
- `requires-python = ">=3.11"`에 dev group만 `>=3.12`로 두면 `uv.lock`의 44개 고정 버전이 그대로다.
  numpy 2.5.3·contourpy 1.4.0이 `>=3.12`라는 문제는 dev 환경과 분석 환경에만 있다.
- Aelix는 같은 확장을 두 번 로드하면(체크아웃 symlink와 설치본) 중복을 걸러 내지 않는다. 두 사본의 hook이 모두
  실행되어 도구 호출마다 telemetry가 두 번 기록되고 맥락 블록이 프롬프트에 두 번 붙는다.
- POSIX 셸 스크립트인 `bin/lab`은 wheel로 설치할 수 없다. 같은 이름의 console script가 조용히 이기고, 복사된
  `bin/lab`은 kit을 찾지 못한다.

## 결정

1. **배포본은 wheel 하나다.** hatchling(1.32.4로 고정)이 체크아웃을 옮기지 않고 한 package로 묶는다:
   `src/aelix_analytics/` → `aelix_analytics/`, `extension/aelix_lab.py` → `aelix_analytics/extension.py`,
   `kit/labkit/` → `aelix_analytics/kit/labkit/`. 새 프로젝트 템플릿은 `kit/template/`에서 `kit/labkit/template/`로
   옮겨 labkit의 package data가 된다. 그래서 자식의 PYTHONPATH에는 `labkit` 하나만 보인다. 개발 환경은
   `package = false`를 유지하고, 배포본의 확인은 언제나 빌드한 wheel로 한다.
2. **진입점은 둘이다.** console script `lab`(`aelix_analytics.cli:main`, Windows에서는 `lab.exe`)과 entry point
   `aelix.extensions: aelix-analytics = aelix_analytics.extension:setup`. manifest는 package 최상위의
   `aelix-plugin.toml`이다: `id = "aelix-analytics"`, `version`은 pyproject·`KIT_VERSION`과 같게, 시작할 때 로드
   (`on_startup_finished`), `[plugin.entry] python`도 적는다. capability는 사실인 네 개(`shell_exec`, `fs_write`,
   `fs_read_user`, `net`)를 참으로 적는다. 스키마는 거짓을 약속으로 읽기 때문이다. 라이선스는 아직 부여된 것이 없으므로
   `UNLICENSED`이고, aelix는 로드할 때마다 경고를 남긴다. 소유자의 라이선스 결정이 이 값을 대신한다.
3. **하한.** 배포본은 `requires-python = ">=3.11"`, dev dependency group은 `[tool.uv.dependency-groups]`로 `>=3.12`.
   분석 프로젝트 환경은 `>=3.12`를 유지한다(ADR-0002 결정 3). 3.11 확인은 lock이 아니라 빌드한 wheel을 새 3.11
   환경에 설치해 한다.
4. **Aelix extra를 두지 않는다.** Aelix 연동은 이 wheel을 aelix의 환경에 설치하는 것이다(`uv tool install aelix
   --with <wheel>` 또는 `aelix extension install <wheel>`). `[aelix]` extra는 aelix-coding-agent 0.1.0b2의
   `pillow<12` 때문에 dev 환경의 pillow를 바꾸고, 이를 막으려면 lock이 커지고 dev와 aelix를 함께 설치할 수 없게 된다.
5. **자원은 resource API로 찾는다.** 설치본의 kit은 `importlib.resources.files("aelix_analytics") / "kit"`이며, 풀린
   파일의 디렉터리여야 한다. zip import는 거절한다: 자식은 zip 안의 kit도 실행하지만, kit은 자기 파일을 읽어
   `env.kit_sha256`을 계산하므로 빈 입력의 해시를 기록하고, aelix 프로세스는 `core`를 파일로 로드한다. kit 안에서
   CLI는 템플릿, 리포트는 assets, 데모는 예제 스크립트를 `importlib.resources.files(__package__)`로 읽는다. aelix
   프로세스 안의 `core`는 resource API를 쓸 수 없으므로(합성 package) 템플릿을 자기 파일 옆에서 찾는다. 호출자가
   경로를 계산하지 않으므로 이 버전부터의 확장과 kit은 버전이 달라도 템플릿 위치가 어긋나지 않는다. 그 전에 복사한
   확장(README의 복사 경로)은 템플릿 경로를 스스로 계산하므로 다시 복사해야 한다. `init`은 무엇이든 만들기 전에
   템플릿부터 읽는다.
6. **확장은 모듈 이름으로 경로를 가린다.** `aelix_analytics.extension`으로 import되면(설치본의 entry point) 5의 설치
   kit을 쓰고 `AELIX_LAB_KIT`를 따르지 않는다. 확장과 kit은 함께 설치된 것이어야 맞물린다. 파일로 로드되면
   `AELIX_LAB_KIT`, 파일 옆의 kit(경로로 로드한 설치본), 체크아웃의 `kit/` 순서다. aelix는 세션을 만들 때마다 확장을
   다시 설정하는데 설치본의 모듈은 프로세스에 한 번만 import되므로, `setup()`이 세션에 속한 상태를 매번 처음부터
   시작한다. 한 aelix runtime에는 사본 하나만 설정된다. 먼저 설정된 사본이 등록을 마친 뒤 runtime의 event bus
   (`aelix.events`)에서 응답하고, 뒤의 사본은 아무것도 등록하지 않고 stderr에 알린다. 이 확인이 없는 이전 버전의 사본은
   응답하지 않으므로 감지되지 않는다.
7. **사람용 진입점은 `lab` 런처다.** stdlib만 쓰고 3.11 이상에서 도는 Python 프로그램으로, `bin/lab`의 계약(명령과
   인자, usage 문구, kit 출력의 바이트 그대로 전달, 종료 코드)을 모든 플랫폼에서 지킨다. `bin/lab`은 체크아웃의 POSIX
   래퍼로 남고 wheel이 설치하지 않는다. 두 구현은 POSIX에서 같은 stand-in uv로 동등성 시험을 거친다. 런처는 자식을
   같은 process group·콘솔과 표준 스트림을 물려준 채 시작해 기다리고, SIGINT(와 SIGBREAK)는 기록만 한다. 터미널의
   Ctrl+C는 이미 자식에게 가 있으므로, 런처는 다시 보내지도 먼저 끝나지도 않는다. 자식을 시작하는 바로 그 순간 온
   Ctrl+C만은 자식이 뜬 뒤 한 번 더 보낸다(그 전에는 자식에게 닿지 않는다; 그 순간 이미 받은 자식에게는 두 번 간다).
   런처만 받은 SIGTERM은 kit의 `run`에만 전한다: run만 그것을 취소로 기록하고, 다른 명령은 도중에 죽으면 쓴 것을
   기록하지 못한다. 그 밖의 단계는 끝까지 두고, 어느 신호든 받은 뒤에는 다음 단계(`lab open`이 문서를 여는 프로그램
   포함)를 시작하지 않는다. 이 SIGTERM 규칙은 POSIX의 것이다. Windows에서는 다른 프로세스가 처리기에 SIGTERM을
   보낼 수 없다.
8. **`ProcessControl`에 두 동작을 더한다.** `open_document(path, *, wait)`와 `find_program(name)`. 둘 다 플랫폼마다
   다르고 자식 프로세스의 일이므로 포트는 여전히 넷이다. macOS는 `open`, 그 밖의 POSIX는 `xdg-open`을 `detect()`가
   고른다. 떼어 놓고 여는 쪽(확장)은 opener가 없으면 main처럼 `webbrowser`를 쓴다. Windows는 `os.startfile`,
   프로그램은 PATH의 절대 경로 항목에서만 찾고 경로로 준 것도 `.exe`만 받는다(작업 디렉터리의 프로그램과, 인자를 다시
   해석하는 `.bat`·`.cmd`를 피한다). 찾은 프로그램은 링크를 따라가지 않은 절대 경로로 실행한다(mise의 shim은 자기
   이름으로 동작을 정하는 링크다). `/lab open`의 `sys.platform` 분기가 이것으로 사라진다.
9. **SDK는 run의 스크립트가 import하는 `labkit`이다.** run을 시작한 진입점(`lab`, `bin/lab`, 확장)이 PYTHONPATH로
   준다. wheel은 최상위 `labkit`을 설치하지 않는다. 노트북·일반 Python에서의 호출은 run을 기록하는 경로가 아니다
   (PRODUCT). run 밖에서도 `lab.note`는 journal 이벤트(작성자 `script`)를 남기고 `lab.save`는 `data/derived`에 쓰며
   `lab.chart`는 기록되지 않는 파일을 `.lab/adhoc`에 남긴다. `import aelix_analytics as analytics`는
   [#9](https://github.com/handochan/aelix-analytics/issues/9)의 이름이다.

## ADR-0002와의 관계

- 결정 2의 "진입점"은 설치 환경에서 도는 것 — 런처, 확장, 확장이 로드하는 `core`·`_platform` — 이다. kit의 명령은
  `bin/lab`과 같이 프로젝트 환경(`>=3.12`)에서 돈다.
- "구현에서 확인할 것" 1(3.11·3.12에서 `python -m labkit` init/import/query/run/note/report/status/context 실행)은
  글자 그대로는 할 수 없다. import·query·demo는 결정 4가 진입 환경에 넣지 않는 분석 라이브러리를 쓰고, 결정 3이
  분석 환경을 `>=3.12`로 둔다. 그래서 두 부분으로 확인한다: 설치 환경의 3.11·3.12에서 런처로 모든 명령을 실행하고
  (명령은 프로젝트 환경에서 돈다), stdlib만 쓰는 명령(init/status/context/note/report)은 3.11에서 `python -m labkit`로도
  실행한다. 이 해석은 소유자가 확인할 미결정 사항으로 남긴다.
- 결정 7의 위치: `bin/lab`은 체크아웃에만 있고, 확장 파일은 설치본에서 `aelix_analytics/extension.py`다.

## 대안과 선택 이유

| 대안 | 검토 결과 |
|---|---|
| uv_build로 빌드하고 소스를 `src/aelix_analytics/` 아래로 옮김 | 체크아웃과 설치본의 배치가 같아지지만 `kit/`·`extension/`이 움직인다: 시험·`bin/lab`·문서 링크 31개·사용자의 symlink가 바뀌고, #9의 개명이 한 번 더 옮긴다. `.gitignore`도 따르지 않는다 |
| setuptools의 `package-dir` | 소스 트리에 egg-info를 쓰고 sdist에 시험 일부만 담으며 기본으로 재현되지 않는다 |
| hatchling의 파일 단위 `sources`로 확장 파일 이름 바꾸기 | 이름이 바뀌지 않고 최상위 `extension/`이 site-packages에 설치됐다(측정). `force-include`를 쓴다 |
| wheel이 최상위 `labkit`을 설치 | 자식의 PYTHONPATH가 site-packages 전체가 되고, PyPI의 무관한 `labkit`과 이름이 겹친다 |
| `.pth`로 kit을 모든 Python의 경로에 추가 | 설치 환경의 모든 프로세스(aelix 포함)의 `sys.path`를 바꾼다 |
| 템플릿을 `kit/template/`에 둠 | 자식에서 labkit의 resource로 닿지 않고, 자식의 PYTHONPATH에 `template`이 namespace package로 보인다 |
| 확장이 설치본의 `aelix_analytics.kit.labkit.core`를 평범하게 import | `labkit/__init__`과 `capture`까지 aelix 프로세스에 들어오고(ADR-0003·계약 A와 다름) kit 경로별 분리가 사라진다 |
| `[aelix]` extra | 결정 4 참고 |
| `[tool.uv] environments`로 lock을 3.12 이상만 | 3.11에서는 `--no-dev`로도 sync할 수 없다 |
| 하한만 `>=3.11`로 바꾸고 uv가 갈라 풀게 둠 | 제품이 쓰지 않는 numpy 2.4.6·contourpy 1.3.3이 lock에 들어간다 |
| `bin/lab`을 wheel의 script로 설치 | 같은 이름의 console script가 조용히 이기고, 복사본은 kit을 찾지 못한다(측정) |
| `bin/lab`이 Python 런처에 위임 | 구현이 하나가 되지만 셸에서 3.11 이상 Python을 찾아야 하고(macOS의 `/usr/bin/python3`는 3.9), POSIX 사용자의 동작이 바뀐다. ADR-0002 결정 6은 `bin/lab`을 셸 래퍼로 둔다 |
| 런처가 `subprocess.run`으로 기다림 | Ctrl+C에서 traceback과 함께 먼저 죽고 uv를 SIGKILL해, kit이 고아가 되거나 기록을 잃는다(측정) |
| 런처가 `SIG_IGN`으로 SIGINT를 무시 | exec을 넘어 상속되어 kit이 중단될 수 없다(측정) |
| 사본 중복을 문서로만 막음 | 사용자 설정 하나로 기록이 두 번 남는다. event bus 확인은 공개 API로 같은 runtime만 본다 |
| 확인에 먼저 응답하고 등록 | 등록 도중 실패한 사본이 다음 사본까지 막는다(설계 리뷰에서 재현) |
| 경로를 `__package__`로 가림 | 점이 든 이름(`-e extension.aelix_lab`, `aelix_lab.v2.py`)이 설치본으로 읽혀 로드되지 않는다(설계 리뷰에서 재현, main 대비 회귀) |
| 설치본도 `AELIX_LAB_KIT`를 따름 | 이전 체크아웃의 kit을 가리키면 새 확장이 없는 동작을 불러 모든 kit 명령이 실패하고, 기록한 메모를 오류로 답한다(설계 리뷰에서 재현) |
| 런처가 모든 자식에 SIGTERM을 전함 | import가 도중에 죽어 기록 없는 데이터셋이나 모든 쿼리를 깨는 임시 parquet이 남는다(설계 리뷰에서 재현) |
| 찾은 uv의 링크를 풂 | mise의 shim이 mise 자체가 되어 `run`을 자기 작업 실행기로 읽는다(Linux에서 재현, main의 확장도 같음) |
| `/lab open`의 `webbrowser` 대비책 제거 | main의 경로를 잃는다. 확장(`wait=False`)은 opener가 없을 때 main처럼 `webbrowser`를 쓰고, 런처(`wait=True`)는 `bin/lab`처럼 알리고 127로 끝난다 |

## 결과와 비용

- 체크아웃의 경로(`bin/lab`, `aelix -e <pack>/extension/aelix_lab.py`, README의 symlink, `PYTHONPATH=<pack>/kit`)는
  그대로다. 설치본과 체크아웃을 함께 쓰면 먼저 로드된 사본 하나만 설정된다. 이 PR 이전 버전의 체크아웃 사본은 이를
  감지하지 못하므로, 설치본을 쓰면 symlink를 지운다(README).
- 확장에는 kit을 찾는 경로가 넷이다(설치본, `AELIX_LAB_KIT`, 경로로 로드한 설치본, 체크아웃). 파일 로드에서는 resource
  API를 쓸 수 없으므로 없앨 수 없다.
- 설치본을 업그레이드하면 aelix를 다시 시작해야 한다. 모듈이 프로세스에 한 번 import되기 때문이다.
- 사람용 진입점이 두 구현이다(`bin/lab`, `lab`). 동등성 시험이 둘을 묶는다. 알려진 차이: 런처는 POSIX에서 SIGTERM을
  kit의 run에 전하고(`bin/lab`의 셸은 kit을 남기고 끝난다), uv가 없을 때 자기 문구로 127, macOS만 `open`을 쓴다. 그 밖의
  작은 차이(빈 첫 인자, 실행할 수 없는 `AELIX_LAB_UV`, 닫힌 파이프, symlink로 들어온 작업 디렉터리의 `init ../DIR`)는
  CONTRACTS F에 있다.
- `env.kit_sha256`이 템플릿 파일도 덮으므로 같은 코드에서 값이 한 번 바뀐다. 기록이 이 값을 비교하지 않는다.
- 빌드에 hatchling이 필요하다(네트워크나 uv 캐시). 체크아웃에 추적되지 않은 파일이 `kit/labkit`·
  `src/aelix_analytics`에 있으면 wheel에 들어가므로, 배포본은 깨끗한 체크아웃에서 빌드하고 시험이 구성원 목록을 확인한다.
- aelix를 다시 설치하면 이 wheel이 빠진다(`uv tool install --force aelix`는 측정, 같은 명령을 `--with` 없이 쓰는
  `install.sh`는 추론). 다시 설치한다.
- Windows의 Ctrl+Break는 uv를 곧바로 끝내므로(PR-1 측정) 런처가 kit의 기록보다 먼저 돌아올 수 있다(추론). 대화형
  콘솔은 Issue #8 PR-3의 범위다.

## 구현에서 확인할 것

1. wheel·sdist의 구성원, METADATA, entry point, manifest 결합과 버전 일치 — `tests/test_packaging.py`.
2. 체크아웃 밖 새 환경(3.11·3.12)에 설치한 뒤 kit·템플릿 조회와 자식의 PYTHONPATH가 정확히 kit인지 — 같은 시험.
3. aelix 0.1.0b2와 함께 설치했을 때 `aelix extension verify`가 BOUND, 로더가 도구 6개·`/lab`·hook 4개를 등록하고 분석
   라이브러리가 aelix 프로세스에 들어오지 않음, 체크아웃 symlink와 함께일 때 사본 하나 — 같은 시험의 end-to-end 단계
   (`AELIX_ANALYTICS_TEST_INSTALL=1`).
4. 설치된 `lab`이 3.11에서 실제 uv로 init → import → query → run → note → report → status → context → open을 수행하고,
   run의 스크립트가 설치된 kit의 `labkit`으로 기록됨 — 같은 단계.
5. `lab`과 `bin/lab`의 계약 동등성, Ctrl+C·SIGTERM, 시작 순간의 Ctrl+C — `tests/test_launcher.py`(POSIX).
6. aelix가 한 프로세스에서 확장을 두 번 설정할 때 설치본 모듈이 같고 상태가 새로 시작하는지, 설치본 확장의 도구
   (`lab_init`·`lab_import`·`lab_run`·`lab_note`)가 실제 uv로 프로젝트를 다루는지 — `tests/test_packaging.py`의 end-to-end 단계.
7. Linux·Windows에서 1–4와 6 — Issue #8의 측정 워크플로. Windows의 대화형 콘솔과 Ctrl+Break는 PR-3.
