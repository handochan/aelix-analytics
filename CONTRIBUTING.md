# 기여 가이드

이 프로젝트는 **에이전트 시대의 새로운 분석 도구·플랫폼인 Aelix Analytics를 실현하는 첫 확장**이며 공식 베타를 준비합니다.
개선의 목적은 사람이 검토할 수 있는 근거와 발견·판단을 남기고, 사람과 에이전트가 분석 프로젝트를 계속 이어 가도록 하는 것입니다.
사람이 CLI·SDK로 직접 분석을 작성하는 것도 정식 사용 경로입니다. 핵심 분석·기록 기능에 Aelix 세션이나 모델 호출을 필수로 추가하지 않습니다.
분석 방향은 목적·지표·현재 질문을 중심으로 유지합니다. 기록이나 차트가 늘어나는 것만으로 분석이 진전했다고 판단하지 않습니다.
먼저 [제품 방향](docs/PRODUCT.md), [현재 계약](docs/CONTRACTS.md), [베타 품질 기준](docs/BETA-QUALITY-GATES.md)을 읽어 주세요.
구조·확장점을 바꾸는 작업은 [아키텍처](docs/ARCHITECTURE.md)와 [ADR-0001](docs/decisions/0001-evolvable-platform-boundaries.md)(2026-10-03 채택)도 확인해 주세요.
[용어집](docs/TERMINOLOGY-AND-LANGUAGE.md)의 Project·Workspace·Visualization·Report 구분과 영어 기본값을 적용해 주세요.
설계 문서의 미구현 API와 디렉터리는 현재 기능이 아닙니다.
Codex·Claude Code의 공통 진입 지침은 [AGENTS.md](AGENTS.md), GitHub 작업·로컬 상태·인수인계 규약은 [운영 정책](docs/PROJECT-OPERATIONS.md)에 있습니다.
작업 보드는 사용자가 지정한 [Project 7](https://github.com/users/handochan/projects/7/views/1)이며, 정확한 연결 정보는 [.github/project.json](.github/project.json)에 있습니다.

## 변경을 제안할 때

작은 수정은 PR에서 문제와 결과를 설명하면 됩니다. 저장 형식·설치·호환성·실행 의미를 바꾸는 작업은
구현 전에 Issue 또는 설계 문서에 영향 범위와 전환 방법을 남겨 주세요.

| PR에 담을 내용 | 질문 |
|---|---|
| 실제 문제 | 어떤 분석·재개·근거 확인 과정에서 막혔나요? |
| 바뀐 행동 | 같은 입력으로 이전과 이후가 어떻게 다른가요? |
| 개선 효과 | 연속성·근거 정확성·사람의 탐색·토큰·지연 중 무엇이 좋아졌나요? |
| 분석 자유도 | 기존에 가능했던 자유 코드·새 라이브러리·그림·방법 변경·오류 복구를 막지는 않나요? |
| 사람의 직접 분석 | 모델 없이 CLI·SDK로 작성·실행·기록할 수 있나요? 사람과 에이전트가 교대로 이어받아도 과거 ID·버전·근거가 유지되나요? |
| 영향 | 기록 호환성, 작은 모델, 설치 환경, 실패 처리에 어떤 영향이 있나요? |
| 도구 표면 | 상시 노출 도구 수·schema 크기·작업 분기가 늘어나나요? 기존 표면 활용과 선택적 노출을 비교했나요? |
| 구조와 확장 | 무엇이 변하는 축이고 어떤 의미 계약은 유지되나요? Strategy·adapter가 필요한 이유와 대체 구현 검증이 있나요? |
| 용어와 언어 | 같은 개념이 코드·UI·문서에서 같은 이름인가요? 영어 기본 label과 사용자 내용·원본 데이터의 언어를 구별했나요? |
| 검증 | 어떤 사례를 확인했고 무엇은 아직 확인하지 않았나요? |

비용과 품질을 함께 비교해 주세요. 토큰이 적어도 중요한 발견을 놓치면 개선이 아닙니다.
현재 telemetry의 호출 수를 토큰 측정값으로 쓰지 말고, 모델·tokenizer·context 설정과 전체 과제 비용을 남겨 주세요.
사용자 데이터·절대 경로·세션 로그를 공개 fixture로 복사하지 말고 재현 가능한 합성 사례를 사용해 주세요.

## 지킬 구조와 의미

- stdlib로 읽는 기록·맥락과 프로젝트 uv 환경에서 실행하는 분석 라이브러리의 경계를 유지합니다.
- 새 코드는 Linux·macOS·Windows를 모두 가정합니다([ADR-0002](docs/decisions/0002-python-support-and-platform-scope.md)). POSIX 전용 API를 새로 도입하지 않고, 잠금·타임아웃·프로세스 생성과 종료·파일 교체·파일명·경로·인코딩은 처음부터 이식 가능한 경계 뒤에 둡니다. 한 플랫폼의 통과를 다른 플랫폼의 결과로 쓰지 않습니다.
- 설치된 자원(kit·템플릿·assets·manifest)은 체크아웃 안의 상대 위치가 아니라 `importlib.resources`로 찾습니다([ADR-0004](docs/decisions/0004-minimal-distribution-and-entry-points.md)). 확장이 aelix 프로세스 안에서 로드하는 `core`·`_platform`·`relations`만은 resource API를 쓸 수 없어(합성 package) `core`가 템플릿을 자기 파일 옆에서 찾습니다. 확장은 셋을 로드할 kit만 정합니다.
- 그 경계는 `src/aelix_analytics/_platform.py`의 네 포트입니다. 구현은 `core.PLATFORM`이 기본값이고, 시험은 `Project(root, platform=…)`로 다른 구현을 주입합니다([ADR-0003](docs/decisions/0003-portable-boundaries-and-in-process-kit-loading.md)). 호출부에 `sys.platform` 분기를 두지 말고, 텍스트를 읽는 파일·자식 프로세스 호출에는 인코딩을 명시하세요. 실행해 보지 않은 플랫폼의 동작은 `not_run`으로 남기고, 무엇을 실행했고 무엇을 실행하지 않았는지는 [현재 계약](docs/CONTRACTS.md) G절 한 곳에 적습니다.
- 새 공개 이름은 `analytics`로 통일하고, 도구의 구·신 별칭을 동시에 상시 노출하지 않습니다. 과거 프로젝트·실행 스냅샷은 별도 reader·명시적 전환으로 보존합니다.
- Workspace는 분석 인터페이스, Visualization은 분석 표현 객체, Chart는 그 유형, Report는 문서 산출물입니다. 데이터 표·근거 그래프를 자동으로 모두 Visualization 기록으로 만들지 않습니다.
- UI·템플릿·속성·오류·접근성 label은 영어를 기본으로 합니다. 사용자 본문·열 이름·보존 코드는 번역하지 않고, Unicode·한국어 조회와 과거 CONTEXT의 읽기를 유지합니다.
- 도메인·응용 계약이 Aelix·UI·저장 backend·분석 라이브러리를 역으로 import하지 않게 합니다. 구현 선택은 진입점에서 조립·주입합니다. 예외는 OS마다 다른 플랫폼 포트로, 위의 `core.PLATFORM`이 그 기본값입니다([ADR-0001](docs/decisions/0001-evolvable-platform-boundaries.md#os마다-다른-구현의-기본값)).
  `tests/test_layers.py`가 출하 코드(`src/aelix_analytics`·`extension/`)의 import 문을 함수 안·조건부까지, 그리고 `DYNAMIC_CALLS`가 이름을 든
  로더의 사용을 다른 이름으로 쓴 것까지 계층표로 검사합니다. 검사가 보지 못하는 것(문자열로 만든 코드의 `exec`, 실행 중에 이름을 만든 로더 등)은
  [아키텍처 §11](docs/ARCHITECTURE.md#계층과-의존-방향-검사)에 있습니다. 새 모듈은 그 파일의 `MODULES`와 같은 절의 계층표에 계층과 함께
  등록합니다(등록하지 않으면 검사가 실패합니다). 표가 허용하지 않는 import는 먼저 의존을 뒤집거나 옮기고, 남기면 `ALLOWLIST`와 그 표에 그
  import가 있는 함수, 이유와 제거 조건을 적습니다.
- 사람용 CLI·SDK와 Aelix 도구는 같은 응용 기능과 기록 계약을 사용합니다. 작성 주체와 실행 수단을 구별하고, 에이전트 세션이 없는 기록도 읽고 이어 갈 수 있어야 합니다.
- 정책은 필요한 Strategy 경계로, 저장·실행·표시·게시는 adapter로 분리합니다. 지표·근거·버전 의미와 공통 기록 검증은 구현별로 복제하지 않습니다.
- journal·run·보존 파일을 정본으로, 프로젝트 탐색 HTML·검색·그래프를 파생물로 다룹니다. 자유 형식 보고서의 본문·첨부는 HTML이어도 보존할 정본입니다. 화면만 아는 관계를 만들지 않습니다.
- 오래된 기록을 덮어쓰지 않고, 스키마를 바꾸면 reader·전환·구버전 거절·원본 보존을 함께 다룹니다.
- 저장소 파일과 kit이 쓰는 텍스트(기록·템플릿 사본·inline 스크립트·Workspace·로그)는 모든 플랫폼에서 UTF-8·LF로 쓰고, 사용자 파일은 바이트 그대로 둡니다([ADR-0007](docs/decisions/0007-line-endings.md), kit 0.4.0). kit이 텍스트 파일을 새로 쓰면 `_platform.open_text`·`_platform.write_text`(`core`가 같은 이름으로 다시 내보냅니다)를 거칩니다(`tests/test_line_endings.py`의 가드가 그 밖의 텍스트 쓰기를 찾습니다). 서드파티 라이브러리가 경로를 받아 스스로 여는 쓰기는 가드가 보지 못하므로 문자열·버퍼로 받아 헬퍼로 씁니다. 남은 한계는 [현재 계약](docs/CONTRACTS.md) G절에 있습니다.
- 프로젝트에 쓰는 진입점(kit 명령, SDK 함수, 확장의 도구·명령·hook)은 첫 부수 효과 — 잠금 파일·디렉터리·캐시·로그 줄 포함 — 전에
  `core.admit`으로 표식을 판정하고, 읽기의 부수 쓰기는 `side=True`로 판정해 거절되면 건너뜁니다([현재 계약](docs/CONTRACTS.md) B, #13).
  기록 함수에는 진입점이 정한 작성 출처 `origin=`(`core.Origin`)을 넘깁니다. 새 쓰기 진입점은 진입점 행렬에 행을 더합니다:
  `tests/test_marker_gate.py`(kit 명령·SDK), `tests/test_extension.py`(확장). 행렬은 진입점을 상수로 두어 새 진입점을 스스로 찾지 못합니다.
  run 기록의 Visualization과 재실행 비교는 `relations.run_visualizations`·`run_reproduction`으로만 읽습니다(`tests/test_run_views.py`의 정적 검사).
- 실행 성공·기록 상태·재실행 비교·분석 검사·결론 검토를 구별합니다. 미확인을 확정으로 올리지 않습니다.
- 목록·상시 맥락·도구 응답은 점진적으로 공개하고 필요한 상세를 더 조회할 수 있게 합니다. 토큰 목표는 성능 측정 기준이며 강제 절단·거절 기준이 아닙니다. 추가 LLM·서버에는 측정 근거가 필요합니다.
- 재사용 코드는 입력·출력·적용 조건을 검사합니다. skill은 사용 절차이며, 작성한 사실만으로 검사나 등록이 완료되지 않습니다.
- skill 제안은 [제안 정책](docs/SKILL-PROPOSAL-POLICY.md)에 따라 옵션화합니다. 요청 시 모드는 자동 검토를 시작하지 않고, 상시 모드도 근거·임계값·빈도를 확인합니다. 토큰 사용량은 별도 지표로 측정합니다.
- 새 기능·skill마다 도구를 추가하지 않습니다. 도구를 하나로 합쳐 거대한 action 선택지를 만드는 것도 피합니다. 선택 정확도·인자 오류·과제 성공·토큰 비용을 함께 비교합니다.
- 선택 기반 후속 분석을 추가할 때는 차트 버전·데이터 스냅샷·선택 단위·행 대응을 먼저 정의합니다. 그래프의 연결선만으로 원본 행 계보를 주장하지 않습니다.
- 지표는 [지표 중심 분석 설계](docs/METRIC-DRIVEN-ANALYSIS.md)에 따라 정의 수정·새 지표 파생·조건별 조회·재계산을 구별합니다. 미정 정의를 허용하되 과거 결과를 최신 정의로 바꾸지 않습니다.
- 기록·지표 관리 때문에 분석법을 닫힌 DSL로 제한하거나 실행 전 수동 등록·승인 단계를 늘리지 않습니다. 자동 캡처와 명시적인 관측 범위로 이해를 돕습니다.
- 지원 하단의 참고 모델은 사용자 지정 Qwen3.8 27B입니다. 실제 실행 설정을 고정해 검증하며 축소 문맥 시험을 제품의 강제 제한으로 바꾸지 않습니다.
- 공유 기능은 [빌드·게시 계약](docs/PUBLISH-AND-SHARING.md)에 따라 작업 리포트·독립 패키지·전달을 구별합니다. 원본 경로 없는 환경, 포함/제외 범위, 진행 중 상태, 버전 연결과 게시 실패를 검증합니다.
- 보고서는 [산출물 설계](docs/REPORT-ARTIFACTS.md)에 따라 자유 형식·중간 작성·버전 보존을 지원합니다. 화면 재생성으로 본문을 덮어쓰거나 분석을 재실행하지 않으며, 보고서 버전과 게시 버전을 구별합니다.

기록 의미가 그대로인 파일 이동은 행동 변경과 분리해 주세요. 새 모듈은 변경 이유와 의존 경계가 분명할 때 나눕니다.
새 포트는 입력·출력 타입뿐 아니라 실패·취소·재시도·지원 능력을 정의하고, 대체 구현에 같은 계약 시험을 적용합니다. 교체 가능한 구현은
`tests/test_contracts.py`의 구현 표에 지원 능력과 함께 등록해 같은 사례를 통과시킵니다.
함수·조합으로 충분한 변경에 형식적인 상속 계층을 추가하지 않습니다. 내부 확장점이 곧바로 공개 plugin API나 모델 도구가 되는 것은 아닙니다.
구현으로 채택된 구조적 결정은 `docs/decisions/`에 남기고 [현재 계약](docs/CONTRACTS.md)을 함께 갱신합니다.

## 현재 개발 검증

```sh
uv sync --locked
uv run --locked pytest -q --timeout 600 --timeout-method=thread
uv run --locked ruff check tests extension src tools
```

pytest-timeout은 `--timeout-method=thread`로 씁니다. 기본 방식(SIGALRM)은 run 가드가 프로세스 안의 run 동안 쓰는 시그널과
겹칩니다.

CI의 `lint`·`platform` 작업은 위 명령에 더해 다음을 돌립니다([저장소 CI](#저장소-ci)). 저장소 시험은 배포본 end-to-end 단계까지
돌고(네트워크 필요, 아래), Windows의 콘솔 시험은 단계를 따로 둡니다. CI는 보고용으로 `-ra`·`--junitxml`을 더합니다(aelix 레그는 `--basetemp`도).

```sh
uv run --locked python tools/check_docs.py --output .devstate/runs/<run-id>/evidence/docs.json
uv run --locked python tools/platform_smoke.py --gate required --output .devstate/runs/<run-id>/evidence/smoke.json
AELIX_ANALYTICS_TEST_INSTALL=1 uv run --locked pytest -q -p no:cacheprovider --timeout 600 --timeout-method=thread --ignore tests/test_windows_console.py
```

Linux 레그는 저장한 공유본의 브라우저 E2E도 돌립니다(#20 PR5). `browser` 의존성 그룹(Playwright)과 브라우저가 필요하며, 엔진을
`AELIX_ANALYTICS_TEST_BROWSER`로 고릅니다. 없으면 이 시험은 건너뜁니다. CI는 Chromium이고, Firefox·WebKit은 손으로 돌립니다
(`AELIX_ANALYTICS_TEST_BROWSER=chromium,firefox,webkit`, `playwright install chromium firefox webkit`).

```sh
uv sync --locked --python 3.12 --group browser
uv run --locked --group browser playwright install chromium
AELIX_ANALYTICS_TEST_BROWSER=chromium uv run --locked --group browser pytest -q -p no:cacheprovider --timeout 600 --timeout-method=thread tests/test_publication_browser.py
```

`floor` 작업은 uv 없이 CPython 3.11과 표준 라이브러리만으로 aelix 프로세스가 로드하는 쪽을 봅니다(분석 라이브러리를 설치하지 않은
3.11에서):

```sh
python3.11 tools/platform_smoke.py --stdlib-only --gate required --output .devstate/runs/<run-id>/evidence/stdlib-smoke.json
```

저장소는 루트 `.gitattributes`로 모든 텍스트를 LF로 체크아웃합니다(ADR-0007). 이 파일이 생기기 전에 만든 Windows 체크아웃은
커밋을 받아도 git이 바뀌지 않은 작업 파일을 다시 쓰지 않아 CRLF로 남고, 거기서 빌드한 wheel과 `kit_sha256`이 다른 플랫폼과 달라
`tests/test_line_endings.py`·`tests/test_packaging.py`의 줄 끝 시험이 실패합니다. 로컬 변경을 먼저 commit하거나 stash한 뒤 한 번
다시 체크아웃하세요(`git rm -r --cached -q . && git reset --hard`). CI는 매번 새로 체크아웃하므로 해당하지 않습니다.

`tests/test_packaging.py`는 `uv build`로 wheel과 sdist를 만들어 체크아웃 밖의 새 환경에 설치합니다(wheel은 3.11·3.12, sdist는 3.12). 빌드
backend(hatchling)와 그 README plugin(hatch-fancy-pypi-readme)은 네트워크나 uv 캐시에서 받습니다. 추적하지 않은 파일이 `src/aelix_analytics`에 있으면
wheel에(package와 실행 루트의 사본 두 곳에) 들어가 구성원 시험이 실패합니다. 소스 트리에서 wheel을 바로 만들면(`uv build --wheel`)
Git이 무시하는 파일(`.demo/`, `__pycache__` 밖의 `*.pyc` 등)도 실행 루트의 사본에 들어갑니다. `force-include`는 `.gitignore`를 보지
않기 때문입니다. 그래서 배포본은 깨끗한 체크아웃에서 빌드하고, 빌드 전에 `git clean -ndx src/aelix_analytics`가 `__pycache__/` 말고
아무것도 보이지 않는지 확인합니다. aelix 0.1.0b2와 실제 uv로 프로젝트를
끝까지 다루는 단계는 `AELIX_ANALYTICS_TEST_INSTALL=1`일 때만 돌며 네트워크가 필요합니다.
다른 파일 시스템(FAT·exFAT 등)에 대한 전환은 그 볼륨에 만든 폴더를 `AELIX_ANALYTICS_TEST_FOREIGN_FS`(여럿이면 PATH처럼 구분)에 줄 때만
`tests/test_migrate_command.py`가 돌립니다. 그 볼륨에 시험용 폴더를 만들고 지우며, 표식을 제자리에 쓰지 않았는지와 원본이 그대로인지,
전환이 커밋해 다시 부르면 `already_migrated`이거나 복사 전 `no_exclusive_rename`으로만 멈추는지(macOS가 만드는 AppleDouble `._<이름>`
때문에 멈추지 않음), 그 볼륨에서 일반 파일 `._x`만 있는 폴더는 init이 받고 폴더 `._d`가 있는 폴더는 init과 전환이 거절하는지, 첫 ledger 줄이
끊긴 전환 디렉터리(macOS가 옆에 `._progress.jsonl`을 씀)를 다시 부른 전환이 지우고 새로 시작하는지를 확인하고, 볼륨이 준 결과는 관찰로
남깁니다. macOS에서는 `hdiutil create -fs "MS-DOS FAT32"`·`-fs ExFAT`로 만든 디스크
이미지를 `hdiutil attach -nobrowse`로 붙여 쓸 수 있습니다.
체크아웃의 `uv sync`는 `[project.scripts]`를 설치하지 않는다는 경고를 냅니다. 정상입니다: 개발 환경은
`package = false`이고, 이 배치는 editable 설치를 지원하지 않으므로 `package = true`로 바꾸지 마세요. 배포본은 빌드한
wheel로 확인합니다.
사람용 진입점은 `bin/analytics`(체크아웃, POSIX)과 설치본의 `analytics`(`src/aelix_analytics/cli.py`) 둘입니다. usage와 메시지의
바이트까지 같은 계약이므로, 한쪽의 명령·인자·출력·종료 코드를 바꾸면 다른 쪽과 `tests/test_launcher.py`의 동등성 시험을 함께 고칩니다.
kit 0.3.0부터 옛 이름의 환경 변수(`LAB_*`·`AELIX_LAB_*`)는 그것을 읽던 쪽이 오류로 거절합니다: `analytics`·`bin/analytics`는
`LAB_PROJECT`·`LAB_ACTOR`·`AELIX_LAB_UV`, kit은 `LAB_PROJECT`·`LAB_ACTOR`·`LAB_SESSION`·`LAB_STYLE`·`AELIX_LAB_TRUST_FILE`, 확장은
`AELIX_LAB_PROJECT`·`AELIX_LAB_UV`·`AELIX_LAB_TRUST_FILE`와 파일 로드의 `AELIX_LAB_KIT`([현재 계약](docs/CONTRACTS.md) A·D). 셸이나 도구 설정에 남아 있으면 그 진입점을
거치는 시험과 측정이 그 오류로 멈추므로 `AELIX_ANALYTICS_*`로 바꾸세요(시험 helper와 `tools/usage_report.py`는 상속한 옛 이름을 지웁니다).

Aelix 통합 시험은 aelix 0.1.0b2(와 그 의존성), pytest, pytest-timeout만 있는 새 환경에서 실행합니다. CI와 같은 격리 환경, 시험
파일, 제한 시간입니다(CI는 보고용으로 `-ra`와 `--junitxml`을 더합니다). pytest·pytest-timeout의 번호는 `uv.lock`의 것과 같습니다:

```sh
uv run --isolated --no-project --python 3.12 --with aelix==0.1.0b2 --with pytest==9.1.1 --with pytest-timeout==2.4.0 \
  python -m pytest -q -p no:cacheprovider --timeout 300 --timeout-method=thread tests/test_extension.py tests/test_asset_extension.py \
  tests/test_checkpoint_extension.py tests/test_usage_report.py tests/test_agent_cli.py
```

이 다섯 파일은 모듈 머리에서 `pytest.importorskip("aelix_coding_agent")`로 aelix가 없으면 건너뜁니다. 그런 파일을 더하면 위 명령과
CI의 두 aelix 레그에 함께 넣습니다. 빠지면 어디에서도 돌지 않습니다(`tests/test_ci_workflow.py`가 확인합니다).

3.11은 `--python 3.11`입니다. `--isolated`를 빼지 마세요. 빼면 3.12에서 uv가 체크아웃의 개발 `.venv` 위에 임시 환경을 얹어
aelix 프로세스가 분석 라이브러리를 import할 수 있게 되고, 이 시험이 보이려는 것(호스트가 분석 라이브러리를 로드하지 않음)이
가려집니다. 런처의 하한 시험은 같은 방식으로 `--python 3.11 --with pytest==9.1.1 --with pytest-timeout==2.4.0`에서
`tests/test_launcher.py`를 실행합니다. 개발 환경의 저장소 시험에는 aelix가 없어 위 다섯 aelix 시험 파일을 건너뜁니다.
`tests/test_agent_cli.py`는 aelix의 확장 로더와 hook 버스로 확장을 실제로 등록하고, aelix의 bash 도구와 `!` 명령 처리로 kit CLI를 불러
기록된 작성 주체를 확인합니다(모델·네트워크·로그인 없음, HOME과 aelix 에이전트 디렉터리는 시험의 임시 디렉터리). 줄 전체가 kit 명령
하나인 `!` 명령과 다른 명령과 합친 줄을 셸을 돌려주는 확장·막는 확장과 두 등록 순서로 나눠 봅니다. `tests/test_extension.py`의 함정 줄
시험은 그 기기에서 찾은 셸(sh·bash·zsh·dash·ksh·pwsh)로 줄을 실제로 돌리므로, 셸이 더 있는 기기에서 더 많은 셸을 덮습니다(Windows에서는
건너뜀; PowerShell 자식은 CI의 Windows 레그가 T22로 돌립니다).

확장이 모델에 보내는 맥락 블록·도구 줄·도구 정의·도구 응답의 크기와, 그 텍스트가 생략한 사실을 다시 읽을 수 있는지는 같은 격리
환경에서 명령 하나로 잽니다(Issue #11). 합성 fixture를 만들어 재고 `usage-report.json`, 그 JSON에서만 만든 `usage-report.md`,
잰 텍스트 그대로인 `texts/`를 씁니다. 신뢰 목록과 uv는 명령의 임시 디렉터리 안의 것만 씁니다. 보고서는 생성물이라 커밋하지 않고,
CI가 push마다 새로 만듭니다(아래 표):

```sh
uv run --isolated --no-project --python 3.12 --with aelix==0.1.0b2 \
  python tools/usage_report.py --output .devstate/runs/<run-id>/evidence/usage
```

토큰은 등록된 tokenizer(`tools/usage_report.py`의 `COUNTERS`: 출처와 파일 sha256)의 파일을 줄 때만 셉니다. 주지 않거나 파일의
sha256이 다르면 모든 토큰 값이 `not_measured`와 그 이유이고, bytes·문자 수를 토큰으로 바꾸지 않습니다. Qwen3.8-27B는 고정
revision의 `tokenizer.json`을 받아 둔 뒤 다음처럼 셉니다:

```sh
uv run --isolated --no-project --python 3.12 --with aelix==0.1.0b2 --with tokenizers==0.23.2 \
  python tools/usage_report.py --output .devstate/runs/<run-id>/evidence/usage \
  --tokenizer qwen3.8-27b=<dir>/tokenizer.json
```

종료 코드는 0(보고서를 씀. 목표 초과와 회수 실패도 데이터로 남김), 1(어떤 표면을 재지 못했거나, 준 tokenizer가 불러지지 않거나
어떤 텍스트를 세다 실패함. 보고서는 씀), 2(사용법 오류)입니다. 등록된 것과 다른 tokenizer 파일이나 없는 `tokenizers` 라이브러리는
실패가 아니라 `not_measured`이고 0입니다. `--output`은 새 디렉터리, 빈 디렉터리, 이전 보고서의 디렉터리(`usage-report.json`이 있음)
중 하나여야 합니다. 그 밖의 비어 있지 않은 디렉터리는 아무것도 지우지 않고 거절합니다(2). 통과·실패는
`tests/test_usage_report.py`가 판정합니다. 모든 필수 사실을 다시 읽을 수 있고, 판정하는 생략 불변식이 모두 성립해야
합니다([계약 A1](docs/CONTRACTS.md#a1-모델에-가는-텍스트의-생략과-다시-읽을-위치)).

시험은 변경에 맞게 선택합니다. 기록·사용량 계측·근거 변경에는 의미 있는 회귀 사례를, UI에는 실제 HTML의 연결과 복귀 확인을 포함합니다.
문서만 바뀌면 링크·예시·현재 코드와의 일치 여부를 검사합니다(`tools/check_docs.py`, CI의 `lint`).

**문서만의 변경**은 시험·도구가 읽지 않는 문서만 바꾼 변경입니다. 다음은 시험의 입력이라, 바꾸면 그 시험을 돌리고 측정 기록의
신선도([운영 정책](docs/PROJECT-OPERATIONS.md#검증-증거의-기준))에서도 코드 변경으로 셉니다.

| 문서 | 읽는 시험·도구 |
|---|---|
| `docs/CONTRACTS.md` | `tests/test_packaging.py`, G절의 선언 문장은 `tools/platform_smoke.py`의 `DECLARED`(`tests/test_platform_smoke.py`) |
| `docs/ARCHITECTURE.md` | 계층표: `tests/test_layers.py` |
| `docs/BETA-QUALITY-GATES.md` | `tests/test_eval_fixtures.py` |
| `docs/schemas/` | 기록·상태 schema: `tests/test_record_contract.py`·`tests/test_writer.py` 등과 `tests/test_doc_checks.py` |
| `docs/templates/WORK-STATE.example.json` | `tests/test_doc_checks.py` |
| `README.md` | wheel의 설명(`pyproject.toml`의 `readme`), `tests/test_packaging.py`, 개발 명령의 aelix 고정값: `tests/test_ci_workflow.py` |
| `CONTRIBUTING.md` | 명령과 고정값: `tests/test_ci_workflow.py` |
| `.github/project.json` | 연결 값: `tools/check_docs.py` |

`src/` 아래의 Markdown(새 프로젝트의 템플릿 등)은 배포본에 들어가는 kit 파일이라 문서가 아닙니다.

현재 동작의 합성 분석 fixture, 사람용 CLI·SDK 회귀와 Aelix 통합의 분리 검증은
[회귀 기준 안내](docs/BASELINE-VERIFICATION.md)를 따릅니다. 실행 증거와 미실행 범위를 함께 남깁니다.

Q17(사람의 직접 분석과 인계)의 환경 준비·모델 호출 계측·기록 대조는 `tools/human_e2e.py`가 합니다([Q17 안내서](tools/human_e2e_data/GUIDE.md), #23).
macOS의 `sandbox-exec`와 unified log를 씁니다.

평가에 쓰는 fixture — Q9의 S/M 규모, Q10의 분석 검사 예제, Q13의 참고 세션 재현 — 는 `tools/eval_fixtures.py`가 만듭니다(#41,
[평가 fixture 안내](tools/eval_fixtures_data/README.md)). 저장소에 커밋하지 않고 측정할 때 같은 seed로 다시 만듭니다. `--out`은 새
디렉터리나 빈 디렉터리여야 합니다. 같은 계획을 두 형식으로 씁니다. 기본인 `--format v1`은 `lab.*/1` 기록이라 kit 0.8.0 전의 kit이
씁니다: `--kit-root`에 그 kit의 실행 루트를 줍니다(`tools/eval_fixtures.py`의 `V1_KIT_COMMIT`, 시험과 CI도 그 커밋을 씀).
`--format v2`는 `analytics.event/2` 기록이라 이 체크아웃의 kit이 씁니다. 정정·철회처럼 형식마다 다르게 읽히는 사례의 판정은
manifest에 형식별로 적힙니다.

```sh
git worktree add --detach <kit> ebd10b644e8cb8550ccf6446b134666db6bb9386   # V1_KIT_COMMIT
uv run --locked python tools/eval_fixtures.py scale --scale S --seed 0 --kit-root <kit>/src --out <dir>   # M은 --scale M
uv run --locked python tools/eval_fixtures.py q10 --seed 0 --kit-root <kit>/src --out <dir>
uv run --locked python tools/eval_fixtures.py q13 --seed 0 --kit-root <kit>/src --out <dir>
uv run --locked python tools/eval_fixtures.py scale --scale S --seed 0 --format v2 --out <dir>   # q10·q13도 --format v2
uv run --locked python tools/eval_fixtures.py check <dir>
uv run --locked python tools/eval_fixtures.py environment --out <file> --disk <dir> --cache-state cold
```

같은 seed는 같은 환경에서 같은 바이트를 만듭니다(실행한 run의 시간·환경, 원본 경로 같은 선언된 휘발 필드 제외. 같은 `--out`이면 실행한
run의 시간만 다릅니다). `manifest.json`의 `digest`로 비교하고, `check`가 digest·개수·사례의 판정·기대 판정 파일을 다시 확인한 뒤
fixture를 cold로 둡니다. `tests/test_eval_fixtures.py`가 두 형식의 S·Q10·Q13을 기본 시험에서 확인합니다. M(fixture 하나에 원본과
`input/`을 합쳐 약 440 MB, 형식마다 두 번 만듦)은 `AELIX_ANALYTICS_TEST_SCALE_M=1`일 때만 돕니다. 생성기가 만드는 내용(계획·사례·데이터)을
바꾸면 `GENERATOR_VERSION`을 올리고 그 시험도 실행하세요.

제한 조회의 결정적 S/M 측정(#15)은 생성기의 fixture를 명시적으로 전환한 합성 사본에서 합니다. 원본 fixture를 고치지 않습니다.
`tools/retrieval_eval.py --fixture <fixture> --project <전환 사본> --out <새 결과 dir> --repeats 10`은 같은 사례의 cold/warm/재구축/직접 읽기,
ID 회수·판단 상태·원본 journal 해시와 UTF-8 응답 bytes를 남깁니다. cold는 파생 인덱스가 없는 경우이며 OS 파일 캐시를 비우지 않습니다.
토큰·실제 모델·브라우저 지연은 미측정/미실행입니다.

분석 자산 host의 Q11 측정(#17)은 `tools/asset_host_eval.py --out <새 결과 dir> --baseline-extension <B3-b 확장 소스>`로
실제 Aelix 여섯 도구의 definition/description/schema와 합성 skill 후보의 요청 시/상시/다음 턴 맥락을 비교합니다.
부모 HOME·USERPROFILE·XDG_CONFIG_HOME·trust 경로를 합성 run에 두고, pinned Aelix 0.1.0b2와 pytest가 있는 Python에서 실행합니다.
`--tokenizer qwen3.8-27b=<등록된 tokenizer.json>`을 주면 usage_report의 고정 hash를 확인해 실제 tokens를 세며, 없거나 다르면
not_measured입니다. JSON·Markdown과 정확한 UTF-8 payload, 합성 프로젝트의 Workspace를 남깁니다. 실제 모델 review usage·지연과
사람이 본 여부는 실행하지 않습니다. host 회귀는 `tests/test_asset_extension.py`, 공용 계약/Workspace는
`tests/test_asset_host.py`·`tests/test_asset_workspace.py`가 확인합니다. CI의 pinned Aelix 3.11/3.12 레그에도 host 사례를 포함합니다.

실제 모델 평가의 과제 12개(2026-10-18 소유자 확인)·정답(2026-10-17 소유자 확인)·모델 설정과 하네스는 `tools/model_eval.py`와
`tools/model_eval_data/`에 있습니다(#40, [실제 모델 평가 안내](tools/model_eval_data/README.md)). 하네스는 과제의 fixture를 위 생성기로 만들고, aelix 0.1.0b2를 `--mode json`으로
띄워 이 checkout의 확장으로 과제를 실행한 뒤 도구 호출·인자 오류·토큰·프로젝트의 변화를 기록하고 정답 파일로 채점합니다. 모델 없이 전
경로를 확인하는 dry-run(스크립트를 따르는 가짜 모델)은 `tests/test_model_eval.py`가 돌립니다(uv로 aelix 0.1.0b2를 받을 수 있을 때, 아니면
건너뜀). 채점 규칙은 리뷰어의 틀린 답·다르게 쓴 바른 답으로 `tests/test_model_eval_scoring.py`가 확인합니다. 신뢰 목록·uv·aelix의 에이전트
디렉터리는 `--out` 안의 것만 쓰고, 모델이 일하는 작업 루트는 `--out` 밖(`--work-root`, 기본은 시스템 임시 디렉터리)에 만들었다가 aelix가
끝나면 run 안으로 옮깁니다(그때 kit이 uv로 만든 프로젝트의 `.venv`는 지웁니다). aelix는 run의 HOME으로 띄우고, 이름이 자격 증명을 뜻하는
변수는 넘기지 않습니다. 하네스는 `~/.aelix` 아래 파일을 열거나 쓰지 않습니다(로그인 파일은 메타데이터만 봄). 정답은 seed 0
fixture의 것이라 다른 `--seed`는 거절합니다.

```sh
uv run --locked python tools/model_eval.py validate --fixtures <dir>
uv run --locked python tools/model_eval.py run --model dry-run --task T06 --out <dir>
uv run --locked python tools/model_eval.py serving-check --model qwen3.8-27b-local   # 고정 설정으로 ollama를 띄운 뒤
uv run --locked python tools/model_eval.py run --model qwen3.8-27b-local --task T06 --out <dir>
uv run --locked python tools/model_eval.py run --model gpt-5.6-sol --task T06 --out <dir>       # 소유자의 aelix 로그인으로
```

프론티어 모델(`gpt-5.6-sol`)은 API 키가 아니라 소유자가 aelix에서 `/login`(OpenAI Codex)으로 해 둔 로그인으로 aelix의
`openai-codex` provider를 거쳐 부릅니다. 하네스는 aelix를 `tools/model_eval_aelix.py`로 띄워 aelix의 자격 증명 저장소 하나만 그
로그인 파일(`--aelix-auth`, 기본 `~/.aelix/agent/auth.json`)로 열게 하고, 나머지 상태는 run 안에 둡니다. 로그인 파일은 aelix만 그
자리에서 읽고 갱신하며, 하네스는 열거나 복사하거나 링크하지 않습니다(있는지와 수정 시각·inode·이름 수만 봄). 로그인이 없으면 실행하지 않고
(`not_run`, 종료 코드 3) 로그인 방법과 실행할 명령을 남깁니다. 모든 run 뒤에는 run 디렉터리 아래의 모든 파일에서 값을 모른 채
자격 증명 모양의 글(JWT, `Bearer` 토큰, 값이 있는 `refresh`·`access`·토큰·키 항목, `sk-` 키, 무작위 긴 토큰)을 찾아 같은 길이 표시로
덮어쓰고 위치만 적으며, 찾으면 `secret_scan: failed`(종료 코드 1)입니다. 덮어쓸 때는 새 파일로 바꿔 쓰고(하드 링크로 이어진 파일에
쓰지 않음), 그 전에 run 밖으로 이어진 심볼릭·하드 링크의 이름을 지웁니다. 그보다 먼저 런처(로그인을 메모리에 가진 aelix 자신의
프로세스)가 aelix가 끝나고 작업 루트가 run으로 옮겨진 뒤 run의 모든 파일·링크 대상·이름에서 로그인의 정확한 값(원래 바이트, JSON
이스케이프, URL 인코딩, base64, 16진수, UTF-16)을 같은 방식으로 가리고 개수와 상대 경로만 알립니다(POSIX, `secret_scan.exact`: 찾거나 하지
못하면 실패). 값은 런처 밖으로 나가지 않습니다. 키 변수를 지정한 모델 설정이면 하네스가 그 키(와 호출자의
자격 증명 변수의 값)를 같은 형태로 덮어씁니다(Windows 포함). 하네스가 신호로 멈추면 진행 중인 run을 검사까지 마치고, `kill -9`로 끝나면 그 run은 `scan OUT`이 검사할 때까지
`not_scanned`로 남습니다. 모델이 작업 루트 밖에 쓴 파일은 찾지 않습니다([실제 모델 평가 안내](tools/model_eval_data/README.md#프론티어-모델)).

문서 검사 명령은 다음과 같습니다. `<run-id>`는 현재 작업의 실제 UUID로 바꿉니다.

```sh
uv run --locked python tools/check_docs.py --output .devstate/runs/<run-id>/evidence/docs.json
```

검사 대상 Markdown·JSON 목록과 내용 해시, 실행 명령·도구 버전·소스 기준이 결과에 포함됩니다. 템플릿 Markdown도 검사합니다.
검사 범위는 Markdown 링크·이미지, HTML의 a/img 참조, Markdown 앵커, JSON·Python 예시의 구문과 개발 state 예시입니다.
외부 URL·그 앵커, 비 Markdown 파일의 fragment, 브라우저 렌더링, 코드 예시의 실행 결과는 범위 밖이며 결과에도 표시합니다. 누락·실패가 있으면 종료 코드 1을 반환합니다.
검사기와 개발 state schema를 바꾸면 `uv run --locked pytest -q tests/test_doc_checks.py`와 관련 lint도 실행합니다.

### 저장소 CI

`.github/workflows/ci.yml`이 저장소 CI입니다.

| 작업 | 언제 | 무엇 | artifact (90일) |
|---|---|---|---|
| `lint` | 모든 PR, main push, 수동·주간 실행 | ruff, 문서 검사 | 문서 검사 결과, 실행 맥락 |
| `platform` | Linux(`ubuntu-24.04`)·Windows(`windows-2025`)·macOS(`macos-26`), `lint`와 같은 때 | 플랫폼 스모크(`tools/platform_smoke.py --gate required`), 저장소 시험(`AELIX_ANALYTICS_TEST_INSTALL=1`, 배포본 end-to-end 포함), aelix 0.1.0b2 통합(CPython 3.12·3.11, [위의 다섯 시험 파일](#현재-개발-검증)), 사용량 보고서(`tools/usage_report.py`, CPython 3.12, 판정 없이 보고만), 런처 시험(CPython 3.11). Linux는 `browser` 의존성 그룹(Playwright)과 Chromium으로 저장한 공유본과 게시 대상 channel 페이지의 브라우저 E2E(`tests/test_publication_browser.py`·`tests/test_publish_browser.py`, `AELIX_ANALYTICS_TEST_BROWSER=chromium`, #20 PR5·#21 PR-3)를 돌립니다. 세 OS 모두 다른 계정이 게시 대상을 읽는지 봅니다(`tools/publish_access_check.py --account`, #21 PR-3). Linux에서는 필수이고, macOS·Windows에서 러너가 계정을 만들지 못하거나 그 계정으로 읽지 못하면(Windows는 그 계정의 로그온 토큰을 얻어 읽습니다) `not_run`으로 적습니다. Windows는 루프백 SMB 공유에도 게시합니다(`--smb`). Windows는 저장소 시험 뒤에 네이티브 콘솔 시험(`tests/test_windows_console.py`, 단계 따로)을, 시험 레그의 마지막에 LongPathsEnabled를 0으로 바꾸고 긴 경로 시험(`tests/test_windows_long_paths.py`)을 새 프로세스로 한 번 더 돌립니다 | 레그별 JUnit, 스모크 JSON(러너 설정 포함), 사용량 보고서(`usage/`: JSON·MD·`texts/`), 시험이 관찰해 남긴 것(`evidence/`, `evidence-long-paths-off/`, aelix 통합 레그의 `evidence-aelix-py3.12/`·`evidence-aelix-py3.11/`; 콘솔 사례는 단언하는 것까지 모두), 다른 계정의 읽기(`publish-access.json`), 인터프리터, aelix 환경의 배포판 목록, 실행 맥락 |
| `floor` | `platform`과 같은 OS | `actions/setup-python`의 CPython 3.11에서 표준 라이브러리만으로 플랫폼 스모크(`--stdlib-only`) | 스모크 JSON, 실행 맥락 |

- 세 OS를 모든 PR·main push·수동 실행(`workflow_dispatch`)·매주 월요일 03:17 UTC 실행에서 돌립니다(2026-10-21 소유자 결정, #22).
  저장소가 공개라 표준 hosted runner는 과금되지 않습니다([GitHub Actions 과금](https://docs.github.com/en/billing/concepts/product-billing/github-actions)).
  그 전(비공개 저장소)에는 macOS를 `ci:macos` 라벨·수동·주간 실행에서만 돌렸고, 그 시기의 기록은 라벨 실행을 인용합니다.
  주간 실행(2026-10-19, `d9413a7`)에서 작업 시간은 `platform` Linux 13분, Windows 47분, macOS 22분이었습니다.
- 고정한 도구: uv `0.11.19`(`astral-sh/setup-uv`), `floor`의 CPython `3.11`(`actions/setup-python`), 격리 레그의 pytest·pytest-timeout은
  `uv.lock`과 같은 번호, aelix는 `0.1.0b2`. Actions는 commit SHA로 고정하고, Dependabot이 Actions의 새 버전을 매달 PR로 올립니다
  (`.github/dependabot.yml`). aelix·Python 의존성은 지원 계약의 고정값이라 Dependabot이 올리지 않습니다. 위 명령과 고정값이 CI와
  같은지는 `tests/test_ci_workflow.py`가 봅니다.
- 시험 레그 하나가 실패해도 다음 레그는 돌고 작업은 실패로 끝납니다. 개발 환경의 sync가 실패하면 `platform`의 시험 레그는 돌지
  않고 작업이 실패합니다. 플랫폼별 작업은 서로 취소하지 않습니다(`fail-fast: false`). 시험·기록 단계마다 제한 시간이 있고, 작업의
  제한 시간은 그 합보다 깁니다(artifact 올리기에는 작업의 제한 시간만 있습니다). 저장소 전체 시험 단계는 45분,
  각 시험은 600초, `platform` 작업은 185분입니다. 단계나 개별 시험의 제한에 걸리면 JUnit이 없거나 일부 사례만
  담길 수 있습니다. 중단된 일부 JUnit은 전체 시험 완료·통과와 구별합니다.
- PR 실행은 GitHub가 만드는 병합 커밋이 아니라 PR 헤드를 체크아웃해 측정합니다. 워크플로 파일은 그 병합 커밋의 것이 쓰이므로,
  실행 맥락(`tools/ci_context.py`)에 `GITHUB_WORKFLOW_SHA`도 남깁니다. aelix 환경의 배포판 목록은 같은 요구 사항을 시험 레그들
  뒤에 다시 풀어 얻은 것입니다.
- main은 ruleset `main`(id 25484515)이 보호합니다(2026-10-21 소유자 결정, #22): PR을 거치지 않은 push, force push, 삭제를 막고,
  병합 전에 필수 검사 일곱 개가 GitHub Actions에서 통과해야 합니다: `lint · ruff and documents`, `platform · ubuntu-24.04`,
  `platform · windows-2025`, `platform · macos-26`, `floor · ubuntu-24.04 · CPython 3.11, stdlib only`,
  `floor · windows-2025 · CPython 3.11, stdlib only`, `floor · macos-26 · CPython 3.11, stdlib only`. macOS의 두 검사는 macOS를 모든 PR에서
  돌리게 한 PR #122가 병합된 뒤 더했습니다(2026-10-21). 그 전에 연 PR은 main을 통합해야 macOS 작업이 생깁니다. 지금 서버의 규칙은
  `gh api repos/handochan/aelix-analytics/rules/branches/main`이 보여 줍니다. 우회할 수 있는 사람은 없고, 승인 리뷰 수는 0입니다(병합
  승인은 [운영 정책](docs/PROJECT-OPERATIONS.md) §4대로 소유자가 합니다). 필수 검사가 통과해도 병합은 리뷰·검증 조건을 채워야 하는
  운영 절차의 일부입니다. aelix의 의존성·PyPI·러너 이미지 같은 상류의 변화로 코드 변경 없이 빨개질 수 있으니, 원인이
  회귀인지 상류 변화인지 구별해 적습니다.
- 장애나 사용 제한으로 CI가 돌지 않은 기간의 플랫폼은 `not_run`이지 통과가 아닙니다.
- 검증 기록이 인용하는 실행은 다시 돌리지(re-run) 않습니다. 다시 돌리면 이전 시도의 artifact를 볼 수 없습니다. 새로 push하거나
  수동 실행을 시작하고, 기록에 실행 번호와 시도(attempt)를 함께 적습니다.
- 플랫폼 스모크는 Windows의 한계를 [현재 계약](docs/CONTRACTS.md) G절의 문장 그대로 읽어 판정합니다(`tools/platform_smoke.py`의
  `DECLARED`). 그 문장을 바꾸면 같은 커밋에서 `DECLARED`도 고칩니다(`tests/test_platform_smoke.py`가 확인합니다). 그래서 그 문장을
  바꾸는 커밋은 측정 기록의 신선도를 따질 때 문서만의 변경이 아닙니다([문서만의 변경](#현재-개발-검증)).
- CI에는 경로 필터가 없습니다. 문서만 바꾼 PR도 모든 작업을 돌리므로 필수 검사가 늘 보고됩니다(2026-10-21 결정, #22).

## 배포와 운영 결정

배포 이름은 `aelix-analytics`입니다(2026-10-17 소유자 결정, [#24](https://github.com/handochan/aelix-analytics/issues/24)). 게시 위치는 PyPI와 GitHub Release이고, 절차는 아래 [릴리스 절차](#릴리스-절차)입니다(2026-10-30 소유자 결정, #24). 아직 게시한 버전은 없습니다.
관리자는 GitHub 핸들 [@handochan](https://github.com/handochan)이며 이메일은 적지 않습니다(2026-10-07 결정, `pyproject.toml`의 `authors`·`maintainers`).
보안 취약점은 GitHub의 비공개 취약점 신고로 받고 공개 Issue로 받지 않습니다([SECURITY.md](SECURITY.md), 2026-10-07 결정). 저장소는 공개이고, 그 신고와 secret scanning·push protection·Dependabot 보안 알림을 켰습니다(2026-10-21, #22). 보안 수정을 받는 버전, 응답 기한과 공지는 [SECURITY.md](SECURITY.md#지원하는-버전과-처리)에 있습니다(2026-10-30 결정, #24).
라이선스는 Apache License 2.0입니다([LICENSE](LICENSE), 2026-09-30 결정).
지원 플랫폼은 Linux·macOS·Windows이며 Python 하한과 패키징 경계는 [ADR-0002](docs/decisions/0002-python-support-and-platform-scope.md)로 채택했습니다. 채택은 구현 완료가 아니므로, 현재 코드의 상태는 [현재 계약](docs/CONTRACTS.md)의 실행 환경 절을 따릅니다.
조합별로 실제 설치·실행한 범위는 [지원 범위](docs/SUPPORT.md)에 증거와 함께 적습니다. 실행하지 않은 조합을 `verified`로 적지 않고, 증거가 검사한 commit 뒤에 코드가 바뀌어도 그 칸을 넓히지 않습니다.
변경 이력은 [CHANGELOG.md](CHANGELOG.md)에 kit 버전별로 적습니다. 사용자에게 보이는 동작·명령·기록·설치를 바꾸는 PR은 병합될 때의 kit 번호 절에 짧은 항목과 PR 링크를 더하고, kit 번호를 올리는 PR은 새 절을 엽니다.
출시 여부는 [품질 게이트](docs/BETA-QUALITY-GATES.md)의 실제 증거로 판단합니다.
작성·실행·기록·설치 경로를 바꾸면 [Q17](docs/BETA-QUALITY-GATES.md#사람의-직접-분석과-작성-주체-전환-검증)의 관련 사례를 검증합니다. 노트북에서 SDK를 import한 사실만으로 실행 기록 연동을 지원한다고 안내하지 않습니다.

### 릴리스 절차

릴리스는 측정한 파일을 그대로 게시합니다. 측정한 뒤에는 다시 빌드한 파일이 그 바이트인지 대조할 뿐, 다른 파일을 올리지 않습니다(2026-10-30 소유자 결정, #24).

1. **후보를 고정합니다.** 후보 PR을 main에 병합합니다. 후보의 번호는 아직 `docs/releases/`에 없는 번호입니다(이미 후보로 기록한 번호면 이 PR에서 올립니다). pyproject·`KIT_VERSION`·manifest·README의 wheel 이름이 한 번호이고, CHANGELOG에 날짜가 있는 그 번호의 절이 있어야 합니다. 그 절은 GitHub Release의 노트가 되므로 링크는 전체 주소로 적습니다(`tools/release_check.py version`). README는 wheel의 설명이 되어 그 버전의 PyPI 페이지에 남습니다. 빌드가 그 상대 링크를 그 버전 tag의 GitHub 주소로 바꾸고(`pyproject.toml`), 이 PR에서 "아직 게시하지 않았다" 같은 시점의 문장을 게시한 뒤에도 맞는 문장으로 바꿉니다.
2. **후보의 파일을 기록합니다.** 병합한 main에서 release workflow를 수동 실행(`workflow_dispatch`)하면 `build`가 Linux에서 wheel·sdist를 만들고 세 OS의 `verify`가 그 파일을 확인합니다. 그 실행의 `release-sums` artifact(`SHA256SUMS`)를 `docs/releases/<번호>.sha256`으로 커밋합니다. 이 파일들이 후보입니다. 빌드는 재현됩니다: 같은 commit을 Linux·macOS에서 다시 빌드하면 같은 바이트가 나오고, Windows에서 빌드한 wheel은 zip 헤더의 '만든 시스템' 칸만 달라 멤버와 그 바이트가 같습니다. 그래서 후보와 게시하는 파일은 Linux에서 만든 것입니다.
3. **후보로 측정합니다.** 출시 리허설(B6)의 결과표와 [지원 범위](docs/SUPPORT.md)를 후보의 sha256으로 채웁니다. 결과표·지원표·측정 기록·`docs/releases/`는 배포본에 들어가지 않으므로 그 뒤의 commit도 같은 파일을 만듭니다. 후보 뒤에 배포본에 들어가는 파일(kit·`extension/aelix_analytics.py`·README·`pyproject.toml`)이 바뀌면 번호를 다시 올리고 1단계부터 다시 하며, 영향을 받는 게이트를 다시 잽니다.
4. **소유자가 출시를 승인하면** `docs/releases/<번호>.sha256`이 있는 main의 commit에 `v<번호>` tag를 붙여 push합니다.
5. **`.github/workflows/release.yml`이 돕니다.**
   1. `build`: tag가 main의 commit에 있고 `v`와 그 번호인지 확인하고, wheel·sdist를 한 번 빌드한 뒤 그 `SHA256SUMS`가 `docs/releases/<번호>.sha256`과 같은지 대조합니다(`tools/release_check.py sums --expect`). 다르면 아무것도 게시하지 않습니다.
   2. `verify`: Linux·Windows·macOS에서 그 파일을 설치·업그레이드·제거합니다(`tests/test_packaging.py`, `AELIX_ANALYTICS_TEST_INSTALL=1`, `AELIX_ANALYTICS_TEST_DIST`). 그 commit과 그 sdist를 다시 빌드한 것이 같은 멤버와 그 바이트인지도 봅니다(Linux·macOS에서는 파일 바이트까지. Windows에서 만든 zip은 헤더가 다릅니다).
   3. `pypi`: 환경 `pypi`의 배포를 소유자가 승인하면 PyPI Trusted Publishing으로 올립니다. 저장한 토큰은 없습니다. 올린 뒤 PyPI의 JSON API가 정확히 그 파일과 sha256을 보일 때까지(최대 10분) 확인합니다(`tools/release_check.py pypi`).
   4. `github-release`: 같은 파일을 `SHA256SUMS`로 대조한 뒤, 그 파일과 `SHA256SUMS`를 GitHub Release에 붙입니다. 노트는 CHANGELOG의 그 번호 절입니다.
6. **게시 뒤에** SECURITY·지원 범위·CHANGELOG의 "아직 게시하지 않았다" 문장과 지원표를 고치는 PR을 올립니다(README는 1단계에서 고쳤습니다).

수동 실행은 어느 ref에서든, tag에서 돌려도 `build`와 `verify`만 돌리고 아무것도 게시하지 않습니다. 후보 기록(2단계)과 tag를 붙이기 전의 리허설입니다.
업로드가 중간에 멈추면(예: wheel만 올라감) 같은 실행의 실패한 job을 다시 돌립니다. 같은 artifact를 쓰고, 이미 있는 파일은 건너뛰며(`skip-existing`), 마지막 확인이 PyPI의 파일이 그 바이트인지 봅니다. 다시 빌드하는 새 실행을 시작하지 않습니다.
PyPI에서 파일이나 릴리스를 지울 수는 있지만, 지운 뒤에도 같은 번호·파일 이름을 다시 쓸 수 없습니다. 잘못 낸 배포는 yank하고 새 번호로 냅니다.

처음 한 번은 소유자가 설정합니다. 둘 다 저장소 밖의 권한이라 이 저장소의 코드로 하지 않습니다.

- PyPI의 pending publisher: project `aelix-analytics`, owner `handochan`, repository `aelix-analytics`, workflow `release.yml`, environment `pypi`.
- GitHub 저장소의 환경 `pypi`: required reviewer `handochan`, 배포할 수 있는 tag `v*`.
