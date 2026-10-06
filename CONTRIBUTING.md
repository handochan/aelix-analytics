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
- 저장소 파일과 kit이 쓰는 텍스트(기록·템플릿 사본·inline 스크립트·Workspace·로그)는 모든 플랫폼에서 UTF-8·LF로 쓰고, 사용자 파일은 바이트 그대로 둡니다([ADR-0007](docs/decisions/0007-line-endings.md), kit 0.4.0). kit이 텍스트 파일을 새로 쓰면 `core.open_text`·`core.write_text`를 거칩니다(`tests/test_line_endings.py`의 가드가 그 밖의 텍스트 쓰기를 찾습니다). 서드파티 라이브러리가 경로를 받아 스스로 여는 쓰기는 가드가 보지 못하므로 문자열·버퍼로 받아 헬퍼로 씁니다. 남은 한계는 [현재 계약](docs/CONTRACTS.md) G절에 있습니다.
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

저장소는 루트 `.gitattributes`로 모든 텍스트를 LF로 체크아웃합니다(ADR-0007). 이 파일이 생기기 전에 만든 Windows 체크아웃은
커밋을 받아도 git이 바뀌지 않은 작업 파일을 다시 쓰지 않아 CRLF로 남고, 거기서 빌드한 wheel과 `kit_sha256`이 다른 플랫폼과 달라
`tests/test_line_endings.py`·`tests/test_packaging.py`의 줄 끝 시험이 실패합니다. 로컬 변경을 먼저 commit하거나 stash한 뒤 한 번
다시 체크아웃하세요(`git rm -r --cached -q . && git reset --hard`). CI는 매번 새로 체크아웃하므로 해당하지 않습니다.

`tests/test_packaging.py`는 `uv build`로 wheel과 sdist를 만들어 체크아웃 밖의 새 환경에 설치합니다(wheel은 3.11·3.12, sdist는 3.12). 빌드
backend(hatchling)는 네트워크나 uv 캐시에서 받습니다. 추적하지 않은 파일이 `src/aelix_analytics`에 있으면
wheel에(package와 실행 루트의 사본 두 곳에) 들어가 구성원 시험이 실패합니다. 소스 트리에서 wheel을 바로 만들면(`uv build --wheel`)
Git이 무시하는 파일(`.demo/`, `__pycache__` 밖의 `*.pyc` 등)도 실행 루트의 사본에 들어갑니다. `force-include`는 `.gitignore`를 보지
않기 때문입니다. 그래서 배포본은 깨끗한 체크아웃에서 빌드하고, 빌드 전에 `git clean -ndx src/aelix_analytics`가 `__pycache__/` 말고
아무것도 보이지 않는지 확인합니다. aelix 0.1.0b2와 실제 uv로 프로젝트를
끝까지 다루는 단계는 `AELIX_ANALYTICS_TEST_INSTALL=1`일 때만 돌며 네트워크가 필요합니다.
체크아웃의 `uv sync`는 `[project.scripts]`를 설치하지 않는다는 경고를 냅니다. 정상입니다: 개발 환경은
`package = false`이고, 이 배치는 editable 설치를 지원하지 않으므로 `package = true`로 바꾸지 마세요. 배포본은 빌드한
wheel로 확인합니다.
사람용 진입점은 `bin/analytics`(체크아웃, POSIX)과 설치본의 `analytics`(`src/aelix_analytics/cli.py`) 둘입니다. usage와 메시지의
바이트까지 같은 계약이므로, 한쪽의 명령·인자·출력·종료 코드를 바꾸면 다른 쪽과 `tests/test_launcher.py`의 동등성 시험을 함께 고칩니다.
kit 0.3.0부터 옛 이름의 환경 변수(`LAB_*`·`AELIX_LAB_*`)는 그것을 읽던 쪽이 오류로 거절합니다: `analytics`·`bin/analytics`는
`LAB_PROJECT`·`LAB_ACTOR`·`AELIX_LAB_UV`, kit은 `LAB_PROJECT`·`LAB_ACTOR`·`LAB_SESSION`·`LAB_STYLE`·`AELIX_LAB_TRUST_FILE`, 확장은
`AELIX_LAB_PROJECT`·`AELIX_LAB_UV`·`AELIX_LAB_TRUST_FILE`와 파일 로드의 `AELIX_LAB_KIT`([현재 계약](docs/CONTRACTS.md) A·D). 셸이나 도구 설정에 남아 있으면 그 진입점을
거치는 시험과 측정이 그 오류로 멈추므로 `AELIX_ANALYTICS_*`로 바꾸세요(시험 helper와 `tools/usage_report.py`는 상속한 옛 이름을 지웁니다).

Aelix 통합 시험은 aelix 0.1.0b2(와 그 의존성), pytest, pytest-timeout만 있는 새 환경에서 실행합니다. CI와 같은 격리 환경과 시험
제한 시간입니다(CI는 보고용으로 `-ra`와 `--junitxml`을 더합니다):

```sh
uv run --isolated --no-project --python 3.12 --with aelix==0.1.0b2 --with pytest==9.1.1 --with pytest-timeout==2.4.0 \
  python -m pytest -q -p no:cacheprovider --timeout 300 --timeout-method=thread tests/test_extension.py tests/test_usage_report.py
```

3.11은 `--python 3.11`입니다. `--isolated`를 빼지 마세요. 빼면 3.12에서 uv가 체크아웃의 개발 `.venv` 위에 임시 환경을 얹어
aelix 프로세스가 분석 라이브러리를 import할 수 있게 되고, 이 시험이 보이려는 것(호스트가 분석 라이브러리를 로드하지 않음)이
가려집니다. 런처의 하한 시험은 같은 방식으로 `--python 3.11 --with pytest==9.1.1 --with pytest-timeout==2.4.0`에서
`tests/test_launcher.py`를 실행합니다. 개발 환경의 저장소 시험에는 aelix가 없어 두 aelix 시험 파일을 건너뜁니다.

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
문서만 바뀌면 링크·예시·현재 코드와의 일치 여부를 검사합니다.

현재 동작의 합성 분석 fixture, 사람용 CLI·SDK 회귀와 Aelix 통합의 분리 검증은
[회귀 기준 안내](docs/BASELINE-VERIFICATION.md)를 따릅니다. 실행 증거와 미실행 범위를 함께 남깁니다.

평가에 쓰는 fixture — Q9의 S/M 규모, Q10의 분석 검사 예제, Q13의 참고 세션 재현 — 는 `tools/eval_fixtures.py`가 만듭니다(#41,
[평가 fixture 안내](tools/eval_fixtures_data/README.md)). 저장소에 커밋하지 않고 측정할 때 같은 seed로 다시 만듭니다. `--out`은 새
디렉터리나 빈 디렉터리여야 합니다.

```sh
uv run --locked python tools/eval_fixtures.py scale --scale S --seed 0 --out <dir>   # M은 --scale M
uv run --locked python tools/eval_fixtures.py q10 --seed 0 --out <dir>
uv run --locked python tools/eval_fixtures.py q13 --seed 0 --out <dir>
uv run --locked python tools/eval_fixtures.py check <dir>
uv run --locked python tools/eval_fixtures.py environment --out <file> --cache-state cold
```

같은 seed는 같은 환경에서 같은 바이트를 만듭니다(실행한 run의 시간·환경, 원본 경로 같은 선언된 휘발 필드 제외). `manifest.json`의
`digest`로 비교하고, `check`가 digest·개수·사례의 판정·기대 판정 파일을 다시 확인합니다. `tests/test_eval_fixtures.py`가 S·Q10·Q13을
기본 시험에서 확인합니다. M(원본 약 217 MB를 두 번 만듦)은 `AELIX_ANALYTICS_TEST_SCALE_M=1`일 때만 돕니다. 생성기를 바꾸면
`GENERATOR_VERSION`을 올리고 그 시험도 실행하세요.

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
| `lint` | 모든 PR과 main push | ruff, 문서 검사 | 문서 검사 결과, 실행 맥락 |
| `platform` | Linux(`ubuntu-24.04`)·Windows(`windows-2025`)는 모든 PR과 main push, macOS(`macos-26`)는 아래의 경우 | 플랫폼 스모크(`tools/platform_smoke.py --gate required`), 저장소 시험(`AELIX_ANALYTICS_TEST_INSTALL=1`, 배포본 end-to-end 포함), aelix 0.1.0b2 통합(CPython 3.12·3.11, `tests/test_extension.py`·`tests/test_usage_report.py`), 사용량 보고서(`tools/usage_report.py`, CPython 3.12, 판정 없이 보고만), 런처 시험(CPython 3.11). Windows는 저장소 시험 뒤에 네이티브 콘솔 시험(`tests/test_windows_console.py`, 단계 따로)을, 시험 레그의 마지막에 LongPathsEnabled를 0으로 바꾸고 긴 경로 시험(`tests/test_windows_long_paths.py`)을 새 프로세스로 한 번 더 돌립니다 | 레그별 JUnit, 스모크 JSON(러너 설정 포함), 사용량 보고서(`usage/`: JSON·MD·`texts/`), 시험이 관찰해 남긴 것(`evidence/`, `evidence-long-paths-off/`; 콘솔 사례는 단언하는 것까지 모두), 인터프리터, aelix 환경의 배포판 목록, 실행 맥락 |
| `floor` | `platform`과 같은 OS | `actions/setup-python`의 CPython 3.11에서 표준 라이브러리만으로 플랫폼 스모크(`--stdlib-only`) | 스모크 JSON, 실행 맥락 |

- macOS runner는 비공개 저장소에서 Linux의 약 10배로 과금되므로 PR에서는 요청할 때만 돕니다. PR에 `ci:macos` 라벨을 붙일
  때마다 그 순간의 헤드로 한 번 돕니다. 그 뒤의 push로는 다시 돌지 않고, 라벨을 떼고 다시 붙이면 앞선 실행을 취소하지 않고 한 번 더
  돕니다. 실행이 시작되면 라벨을 떼어 둡니다. 라벨 실행에는 Linux·Windows 작업이 없고 `lint`는 건너뜀(skipped)으로
  표시됩니다(`ci:macos`가 아닌 라벨이면 모든 작업이 건너뜀). 건너뜀은 검증 결과가 아닙니다. 수동 실행(`workflow_dispatch`, `macos` 선택)과 매주 월요일 03:17 UTC 실행은 세 OS를 모두 돌립니다.
- 비용(GitHub Free의 월 2,000분 기준): 이 워크플로의 첫 측정(PR #34)에서 Linux+Windows 한 번은 작업마다 분 단위로 올리고
  Windows를 2배로 세어 58분, macOS 한 번은 10배로 세어 90분이었습니다. 주간 실행만으로 월 약 650분이 듭니다(추정). 레그가 멈춰
  제한 시간까지 가면 작업 하나가 제한 시간만큼(`platform` 150분, macOS면 그 10배) 과금될 수 있습니다.
- 시험 레그 하나가 실패해도 다음 레그는 돌고 작업은 실패로 끝납니다. 개발 환경의 sync가 실패하면 `platform`의 시험 레그는 돌지
  않고 작업이 실패합니다. 플랫폼별 작업은 서로 취소하지 않습니다(`fail-fast: false`). 시험·기록 단계마다 제한 시간이 있고, 작업의
  제한 시간은 그 합보다 깁니다(artifact 올리기에는 작업의 제한 시간만 있습니다). 단계의 제한이나 시험 하나의 제한(pytest-timeout)에 걸린 레그는 JUnit을 남기지 않습니다.
- PR 실행은 GitHub가 만드는 병합 커밋이 아니라 PR 헤드를 체크아웃해 측정합니다. 워크플로 파일은 그 병합 커밋의 것이 쓰이므로,
  실행 맥락(`tools/ci_context.py`)에 `GITHUB_WORKFLOW_SHA`도 남깁니다. aelix 환경의 배포판 목록은 같은 요구 사항을 시험 레그들
  뒤에 다시 풀어 얻은 것입니다.
- 서버가 강제하지 않습니다. 이 요금제에서 branch protection·ruleset 조회는 HTTP 403입니다([운영 정책](docs/PROJECT-OPERATIONS.md) §9).
  빨간 실행을 병합하지 않는 것은 운영 규칙입니다. aelix의 의존성·PyPI·러너 이미지 같은 상류의 변화로 코드 변경 없이 빨개질 수
  있으니, 원인이 회귀인지 상류 변화인지 구별해 적습니다.
- Actions 사용량이 바닥나면 CI가 돌지 않습니다. 그 기간의 플랫폼은 `not_run`이지 통과가 아닙니다.
- 검증 기록이 인용하는 실행은 다시 돌리지(re-run) 않습니다. 다시 돌리면 이전 시도의 artifact를 볼 수 없습니다. 새로 push하거나
  라벨을 다시 붙이고, 기록에 실행 번호와 시도(attempt)를 함께 적습니다.
- 플랫폼 스모크는 Windows의 한계를 [현재 계약](docs/CONTRACTS.md) G절의 문장 그대로 읽어 판정합니다(`tools/platform_smoke.py`의
  `DECLARED`). 그 문장을 바꾸면 같은 커밋에서 `DECLARED`도 고칩니다(`tests/test_platform_smoke.py`가 확인합니다). 그래서 그 문장을
  바꾸는 커밋은 측정 기록의 신선도를 따질 때 문서만의 변경이 아니고, 앞으로 CI에 경로 필터를 두더라도 `docs/CONTRACTS.md`를
  빼지 않습니다.

## 첫 공개 전에 남은 운영 결정

배포 이름, 유지관리 책임자, 보안 신고 경로와 릴리스 절차는 확정해 공개해야 합니다.
라이선스는 Apache License 2.0입니다([LICENSE](LICENSE), 2026-09-30 결정).
지원 플랫폼은 Linux·macOS·Windows이며 Python 하한과 패키징 경계는 [ADR-0002](docs/decisions/0002-python-support-and-platform-scope.md)로 채택했습니다. 채택은 구현 완료가 아니므로, 현재 코드의 상태는 [현재 계약](docs/CONTRACTS.md)의 실행 환경 절을 따릅니다.
출시 여부는 [품질 게이트](docs/BETA-QUALITY-GATES.md)의 실제 증거로 판단합니다.
작성·실행·기록·설치 경로를 바꾸면 [Q17](docs/BETA-QUALITY-GATES.md#사람의-직접-분석과-작성-주체-전환-검증)의 관련 사례를 검증합니다. 노트북에서 SDK를 import한 사실만으로 실행 기록 연동을 지원한다고 안내하지 않습니다.
