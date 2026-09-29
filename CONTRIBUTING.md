# 기여 가이드

이 프로젝트는 **에이전트 시대의 새로운 분석 도구·플랫폼인 Aelix Analytics를 실현하는 첫 확장**이며 공식 베타를 준비합니다.
개선의 목적은 사람이 검토할 수 있는 근거와 발견·판단을 남기고, 사람과 에이전트가 분석 프로젝트를 계속 이어 가도록 하는 것입니다.
사람이 CLI·SDK로 직접 분석을 작성하는 것도 정식 사용 경로입니다. 핵심 분석·기록 기능에 Aelix 세션이나 모델 호출을 필수로 추가하지 않습니다.
분석 방향은 목적·지표·현재 질문을 중심으로 유지합니다. 기록이나 차트가 늘어나는 것만으로 분석이 진전했다고 판단하지 않습니다.
먼저 [제품 방향](docs/PRODUCT.md), [현재 계약](docs/CONTRACTS.md), [베타 품질 기준](docs/BETA-QUALITY-GATES.md)을 읽어 주세요.
구조·확장점을 바꾸는 작업은 [아키텍처](docs/ARCHITECTURE.md)와 [ADR-0001 초안](docs/decisions/0001-evolvable-platform-boundaries.md)도 확인해 주세요.
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
- 설치된 자원(kit·템플릿·assets·manifest)은 체크아웃 안의 상대 위치가 아니라 `importlib.resources`로 찾습니다([ADR-0004](docs/decisions/0004-minimal-distribution-and-entry-points.md)). 확장이 aelix 프로세스 안에서 로드하는 `core`·`_platform`만은 resource API를 쓸 수 없어 호출자가 위치를 넘깁니다.
- 그 경계는 `kit/labkit/_platform.py`의 네 포트입니다. 구현은 `core.PLATFORM`이 기본값이고, 시험은 `Project(root, platform=…)`로 다른 구현을 주입합니다([ADR-0003](docs/decisions/0003-portable-boundaries-and-in-process-kit-loading.md)). 호출부에 `sys.platform` 분기를 두지 말고, 텍스트를 읽는 파일·자식 프로세스 호출에는 인코딩을 명시하세요. 실행해 보지 않은 플랫폼의 동작은 `not_run`으로 남기고, 무엇을 실행했고 무엇을 실행하지 않았는지는 [현재 계약](docs/CONTRACTS.md) G절 한 곳에 적습니다.
- 새 공개 이름은 `analytics`로 통일하고, 도구의 구·신 별칭을 동시에 상시 노출하지 않습니다. 과거 프로젝트·실행 스냅샷은 별도 reader·명시적 전환으로 보존합니다.
- Workspace는 분석 인터페이스, Visualization은 분석 표현 객체, Chart는 그 유형, Report는 문서 산출물입니다. 데이터 표·근거 그래프를 자동으로 모두 Visualization 기록으로 만들지 않습니다.
- UI·템플릿·속성·오류·접근성 label은 영어를 기본으로 합니다. 사용자 본문·열 이름·보존 코드는 번역하지 않고, Unicode·한국어 조회와 과거 CONTEXT의 읽기를 유지합니다.
- 도메인·응용 계약이 Aelix·UI·저장 backend·분석 라이브러리를 역으로 import하지 않게 합니다. 구현 선택은 진입점에서 조립·주입합니다.
- 사람용 CLI·SDK와 Aelix 도구는 같은 응용 기능과 기록 계약을 사용합니다. 작성 주체와 실행 수단을 구별하고, 에이전트 세션이 없는 기록도 읽고 이어 갈 수 있어야 합니다.
- 정책은 필요한 Strategy 경계로, 저장·실행·표시·게시는 adapter로 분리합니다. 지표·근거·버전 의미와 공통 기록 검증은 구현별로 복제하지 않습니다.
- journal·run·보존 파일을 정본으로, 프로젝트 탐색 HTML·검색·그래프를 파생물로 다룹니다. 자유 형식 보고서의 본문·첨부는 HTML이어도 보존할 정본입니다. 화면만 아는 관계를 만들지 않습니다.
- 오래된 기록을 덮어쓰지 않고, 스키마를 바꾸면 reader·전환·구버전 거절·원본 보존을 함께 다룹니다.
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
새 포트는 입력·출력 타입뿐 아니라 실패·취소·재시도·지원 능력을 정의하고, 대체 구현에 같은 계약 시험을 적용합니다.
함수·조합으로 충분한 변경에 형식적인 상속 계층을 추가하지 않습니다. 내부 확장점이 곧바로 공개 plugin API나 모델 도구가 되는 것은 아닙니다.
구현으로 채택된 구조적 결정은 `docs/decisions/`에 남기고 [현재 계약](docs/CONTRACTS.md)을 함께 갱신합니다.

## 현재 개발 검증

```sh
uv sync
uv run pytest -q
uv run ruff check kit tests extension src tools
```

`tests/test_packaging.py`는 `uv build`로 wheel과 sdist를 만들어 체크아웃 밖의 새 환경(3.11·3.12)에 설치합니다. 빌드
backend(hatchling)는 네트워크나 uv 캐시에서 받습니다. 추적하지 않은 파일이 `kit/labkit`·`src/aelix_analytics`에 있으면
wheel에 들어가 구성원 시험이 실패하므로, 배포본은 깨끗한 체크아웃에서 빌드합니다. aelix 0.1.0b2와 실제 uv로 프로젝트를
끝까지 다루는 단계는 `AELIX_ANALYTICS_TEST_INSTALL=1`일 때만 돌며 네트워크가 필요합니다.
체크아웃의 `uv sync`는 `[project.scripts]`를 설치하지 않는다는 경고를 냅니다. 정상입니다: 개발 환경은
`package = false`이고, 이 배치는 editable 설치를 지원하지 않으므로 `package = true`로 바꾸지 마세요. 배포본은 빌드한
wheel로 확인합니다.
사람용 진입점은 `bin/lab`(체크아웃, POSIX)과 설치본의 `lab`(`src/aelix_analytics/cli.py`) 둘입니다. 한쪽의 명령·인자·
출력·종료 코드를 바꾸면 다른 쪽과 `tests/test_launcher.py`의 동등성 시험을 함께 고칩니다.

Aelix 통합 시험은 Aelix와 pytest가 설치된 별도 환경에서도 실행합니다. 예를 들어 이 저장소와 기존 작업공간 배치에서는:

```sh
../../aelix-ai/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_extension.py -o addopts=""
```

이는 개발 체크아웃용 예입니다. 첫 베타의 CI는 특정 개발자의 경로에 의존하지 않고 고정된 Aelix 버전의 새 환경을 준비해야 합니다.
시험은 변경에 맞게 선택합니다. 기록·사용량 계측·근거 변경에는 의미 있는 회귀 사례를, UI에는 실제 HTML의 연결과 복귀 확인을 포함합니다.
문서만 바뀌면 링크·예시·현재 코드와의 일치 여부를 검사합니다.

현재 동작의 합성 분석 fixture, 사람용 CLI·SDK 회귀와 Aelix 통합의 분리 검증은
[회귀 기준 안내](docs/BASELINE-VERIFICATION.md)를 따릅니다. 실행 증거와 미실행 범위를 함께 남깁니다.

문서 검사 명령은 다음과 같습니다. `<run-id>`는 현재 작업의 실제 UUID로 바꿉니다.

```sh
uv run --locked python tools/check_docs.py --output .devstate/runs/<run-id>/evidence/docs.json
```

검사 대상 Markdown·JSON 목록과 내용 해시, 실행 명령·도구 버전·소스 기준이 결과에 포함됩니다. 템플릿 Markdown도 검사합니다.
검사 범위는 Markdown 링크·이미지, HTML의 a/img 참조, Markdown 앵커, JSON·Python 예시의 구문과 개발 state 예시입니다.
외부 URL·그 앵커, 비 Markdown 파일의 fragment, 브라우저 렌더링, 코드 예시의 실행 결과는 범위 밖이며 결과에도 표시합니다. 누락·실패가 있으면 종료 코드 1을 반환합니다.
검사기와 개발 state schema를 바꾸면 `uv run --locked pytest -q tests/test_doc_checks.py`와 관련 lint도 실행합니다.

## 첫 공개 전에 남은 운영 결정

라이선스, 배포 이름, 유지관리 책임자, 보안 신고 경로와 릴리스 절차는 확정해 공개해야 합니다.
지원 플랫폼은 Linux·macOS·Windows이며 Python 하한과 패키징 경계는 [ADR-0002](docs/decisions/0002-python-support-and-platform-scope.md)로 채택했습니다. 채택은 구현 완료가 아니므로, 현재 코드의 상태는 [현재 계약](docs/CONTRACTS.md)의 실행 환경 절을 따릅니다.
현재 LICENSE가 없으므로 이 문서가 사용·재배포의 라이선스를 부여하지 않습니다.
출시 여부는 [품질 게이트](docs/BETA-QUALITY-GATES.md)의 실제 증거로 판단합니다.
작성·실행·기록·설치 경로를 바꾸면 [Q17](docs/BETA-QUALITY-GATES.md#사람의-직접-분석과-작성-주체-전환-검증)의 관련 사례를 검증합니다. 노트북에서 SDK를 import한 사실만으로 실행 기록 연동을 지원한다고 안내하지 않습니다.
