# Aelix Analytics — 에이전트 시대의 분석

**사람과 에이전트가 검증 가능한 분석과 프로젝트를 함께 이어 가는 새로운 분석 도구·플랫폼을 만듭니다.**
Jupyter Notebook과 BI가 만들어 온 분석 작업방식을 바탕으로, 목적·질문·지표·데이터·처리·실행·시각화·발견·판단·후속 분석을
연결합니다. 대화나 모델이 바뀌어도 근거를 확인하고 분석을 계속할 수 있는 새로운 분석 패러다임이 목표입니다.

분석 방향은 목적과 지표를 중심으로 유지합니다. 모호한 지표의 구체화, 여러 지표의 병행과 파생을 기록하고,
각 탐색·계산·발견을 현재 질문과 다음 판단에 연결하는 것이 설계의 중심입니다.

이 Aelix 확장은 그 비전의 첫 제품 형태이며 **공식 베타 출시를 준비**합니다.
베타 이후의 지속적인 발전을 위해 지표·근거·버전 계약을 보존하고, 정책·저장소·실행·게시 구현을 교체할 수 있는 구조를 설계합니다.
이 저장소는 비교 실험용 `aelix-analytics-baseline`에서 출발해 2026-09-18에 정식 제품 이름인
`aelix-analytics`로 변경했습니다. 과거 평가 문서에는 당시의 baseline 명칭이 이력으로 남아 있습니다.
아래 사용법은 현재 구현 기준입니다. 새 인사이트 화면·Reports 탭은 아직 설계 단계입니다. 제한 조회·분석 자산과 checkpoint 인계는 현재 CLI·SDK·도구에서 제공합니다.
분석 도중에도 자유 형식 보고서를 만들고, 작성 시점·근거·버전과 컬럼별 반복 보고서 묶음을 관리하는 흐름을 설계합니다.
진행 중인 분석을 팀원에게 전달하는 독립 HTML 빌드와 `publish` 흐름도 설계에 포함합니다.
공식 명칭은 `analytics` 명령·SDK, 분석 인터페이스 `Workspace`, 시각화 객체 `Visualization`, 문서 산출물 `Report`로 정리합니다.
kit이 보이는 글(Workspace·도구 응답·오류·새 프로젝트 템플릿·데모)은 영어이고, 사람이 쓴 목표·발견·제목·열 이름·데이터·경로는 쓴
그대로 보입니다(#10, [용어 §7](docs/TERMINOLOGY-AND-LANGUAGE.md#7-영어-기본값과-다국어-내용)). 명령·도구·SDK·환경 변수의 이름은 kit 0.3.0에서
`analytics`로 바뀌었고, 저장 이름은 kit 0.8.0의 새 기록 형식(#13)에서 바뀌었습니다: 새 프로젝트는 `analytics.json`·`.analytics/`·
`workspace/`·`analytics.*` 기록입니다. 그 전의 `lab.json` 프로젝트는 읽기만 하고, `analytics migrate`로 새 폴더에 복사해 이어 씁니다
([옛 이름에서 옮기기](#옛-이름에서-옮기기), [옛 형식 프로젝트 옮기기](#옛-형식-프로젝트-옮기기-analytics-migrate)).

사람이 Python·SQL로 직접 분석하는 경로도 정식 사용 시나리오로 설계합니다. 현재도 사람용 `bin/analytics` CLI로 Aelix 없이 분석을 실행·기록할 수 있습니다.
베타에서는 모델 없이 프로젝트 생성부터 발견·보고서 기록과 결과 확인까지 완료하고, 사람 → 에이전트 → 사람이 같은 근거를 이어받는지 검증합니다.
[직접 분석 설계](docs/DESIGN-CONTINUITY-AND-REUSE.md#사람이-직접-분석하는-경로)와 [Q17 검증 기준](docs/BETA-QUALITY-GATES.md#사람의-직접-분석과-작성-주체-전환-검증)에 범위를 명시합니다. 노트북 실행 기록 연동과 브라우저 분석 편집기는 후속 범위입니다.

그래프 표현의 도입 방향은 [분석 근거 그래프 제안 0.2](docs/PROPOSAL-EVIDENCE-GRAPH-2026-09-17.md)에 기록되어 있습니다.
각 요소에서 관계를 확인하고 정확한 버전으로 이동하는 흐름, 현재 기록의 한계와 단계별 도입 범위를 담은 후속 제안입니다.

자유로운 Python 분석(DuckDB·Polars·matplotlib·plotly), 프로젝트별 uv 환경, 파일로 읽을 수 있는 기록과 정적 HTML을
유지합니다. 개선의 기준은 분석의 연속성, 근거의 정확성, 사람의 탐색 편의, 에이전트의 문맥·토큰 비용입니다.
모델이 새로운 방법·코드·라이브러리·그림을 선택할 자유를 보존합니다. 지원 설계의 하단 기준은 사용자 지정 **Qwen3.8 27B**이며,
실제 모델·설정별 검증은 아직 필요합니다. 작은 문맥 수치로 모델의 능력이나 분석 범위를 강제 제한하지 않습니다.

인접한 [Aelix Analytics Engine](https://github.com/handochan/aelix-analytics-engine)은 원장·불변 번들·선택·검증·복구의 엄격한 계약을
검증하는 기반 프로젝트입니다. 이 저장소가 사용자 제품과 공식 확장의 대표 이름을 사용합니다.

- 분석은 HTML 리포트로 봅니다. 모든 차트에서 **그 차트를 만든 코드(실행된 바이트 그대로, 줄 강조)**,
  **입력 데이터(sha256, 지금 파일과 비교)**, **차트에 첨부한 데이터(parquet + 미리보기)** 로 바로 이동합니다.
- 데이터는 Parquet, 처리는 DuckDB·Polars입니다.
- 프로젝트 맥락(CONTEXT.md, 버전 기록), 중요 정보(발견·결정·가정·열린 질문), 시간순 이력(journal)을
  파일로 남기고, 리포트와 에이전트 프롬프트에 함께 보여 줍니다.

> 샌드박스가 아닙니다. aelix는 확장 도구를 실행하기 전에 승인을 묻지 않으므로 `analytics_run`은 확인 없이
> 코드를 실행합니다. 실데이터에는 이 점을 알고 쓰세요. [한계](#한계)를 먼저 읽으세요.

## 공식 베타 준비 문서

- [제품 방향](docs/PRODUCT.md): 정체성, 분석 발견·재사용 제안·사용 중 문제의 구별, 개선 기준.
- [플랫폼 아키텍처](docs/ARCHITECTURE.md): Strategy 적용 영역, 포트·어댑터·의존 방향, 장기 호환성과 공개 계약, 첫 구현의 검증 기준.
- [용어·기본 언어](docs/TERMINOLOGY-AND-LANGUAGE.md): 제품·도메인·상태·코드·UI의 공통 어휘, 영어 기본값과 다국어 내용 보존.
- [저장소 전체 용어 점검](docs/REVIEW-TERMINOLOGY-2026-09-18.md): CLI·SDK·저장 형식·시각화·템플릿·데모·시험의 변경 목록과 legacy 전환 기준.
- [메모리·skill 벤치마킹](docs/RESEARCH-MEMORY-AND-SKILLS-2026-09-17.md): Hermes 구현, Letta·Graphiti·Mem0와 관련 논문, 현재 코드에서 확인한 한계.
- [제품화 참고 세션 검토](docs/REVIEW-REFERENCE-SESSION-2026-09-18.md): 실제 분석의 전개·오류 복구·지표 형성, 보존할 자유도와 기록 개선점.
- [보고서 산출물 설계](docs/REPORT-ARTIFACTS.md): Reports 탭, 중간·자유 형식 보고서, 시점·버전·근거 보존, 컬럼별 반복 생성과 공유.
- [빌드·공유·게시 설계](docs/PUBLISH-AND-SHARING.md): 독립 HTML/ZIP, 공유 범위, 진행 중 상태, 버전별·최신 게시 주소, 팀 접근과 갱신.
- [맥락·재사용·탐색 설계](docs/DESIGN-CONTINUITY-AND-REUSE.md): 기록·검색·관계 그래프·HTML 동선, 토큰 사용 성능 측정, 배포 구조와 호환성.
- [지표 중심 분석 설계](docs/METRIC-DRIVEN-ANALYSIS.md): 분석 목적과 현재 지표·질문의 연결, 모호한 정의·여러 지표·수정·파생·관측의 구별.
- [Skill 제안 정책](docs/SKILL-PROPOSAL-POLICY.md): 요청 시/상시 모드, 제안 임계값·빈도·별도 비용 측정. 기본 제안은 사용자 요청 시에만 수행.
- [베타 품질 기준과 순서](docs/BETA-QUALITY-GATES.md): 출시 전 검증, 작은 문맥·규모 시험, 재실행·복구·멱등성의 후속 우선순위.
- [지원 범위](docs/SUPPORT.md): Python·운영체제·aelix·설치 경로별로 실제 설치·실행한 범위, 그 증거와 알려진 한계.
- [변경 이력](CHANGELOG.md): kit 버전별 변경. 아직 게시한 버전은 없습니다.
- [보안 정책](SECURITY.md): 취약점을 비공개로 알리는 방법과 범위.
- [기여 가이드](CONTRIBUTING.md): 변경의 개선 효과와 검증 근거를 남기는 방법.
- [프로젝트 운영 정책](docs/PROJECT-OPERATIONS.md): Codex·Claude Code 공통 지침, GitHub Issue/Project 관리, 로컬 재개 상태와 인수인계.

제안된 필드·명령·구조는 구현 완료를 뜻하지 않습니다. 현재 기능과 보장 범위는 [현재 계약](docs/CONTRACTS.md)을 따릅니다.

## 5분 시작

```sh
PACK=/path/to/aelix-analytics                   # 이 저장소를 받은 위치

# 1) 모델 없이 결과물 모양 보기: 합성 데이터 + 예제 run 3개 + 메모 + 재실행 검증
$PACK/bin/analytics demo ~/analyses/demo        # 처음엔 uv 환경 준비로 1분 안팎
open ~/analyses/demo/workspace/index.html

# 2) 그 프로젝트에서 aelix로 이어서 분석
cd ~/analyses/demo
aelix -e $PACK/extension/aelix_analytics.py
```

aelix 안에서: `/analytics`(현황) · `/analytics open`(리포트) · "지역별 성장률 차트에 플롯 데이터도 기록되게 고쳐줘" 같은 요청.

직접 분석에 필요한 것: [uv](https://docs.astral.sh/uv/)(PATH 또는 `AELIX_ANALYTICS_UV`). Aelix 연동에는 추가로 aelix 0.1.0b2 이상이 필요합니다.
지원 플랫폼은 Linux·macOS·Windows를 목표로 합니다. 측정한 조합(Windows는 Windows Server 2025의 한 구성)과 Windows의 한계, 아직 실행하지 않은 것(사람이 누른 콘솔 키, Windows의 실제 aelix 세션, 문서를 실제로 여는 프로그램 등)은 [현재 계약](docs/CONTRACTS.md)의 F·G절에 있습니다. Python·운영체제·aelix·설치 경로별로 실제 설치·실행한 범위와 그 증거는 [지원 범위](docs/SUPPORT.md)에 있습니다. Windows 콘솔의 Ctrl+C·Ctrl+Break는 의사 콘솔과 고전 콘솔에서 측정했습니다. Windows에서 알아 둘 것은 [Windows에서 쓸 때](#windows에서-쓸-때)에 있습니다.
분석 라이브러리는 **프로젝트마다 자기 uv 환경**에 설치되며 aelix 환경은 건드리지 않습니다.

## 설치본(wheel)으로 쓰기

체크아웃 없이 쓰는 방법입니다. Windows에서는 이것이 사람용 명령을 얻는 길입니다(`bin/analytics`는 POSIX 셸 스크립트).
배포본은 아직 어디에도 게시하지 않았으므로 체크아웃에서 직접 만듭니다.

```sh
cd $PACK && uv build                                   # dist/aelix_analytics-0.18.0-py3-none-any.whl
uv tool install --python 3.12 dist/aelix_analytics-0.18.0-py3-none-any.whl        # 사람용 `analytics` (Windows: analytics.exe)
uv tool install --python 3.12 aelix==0.1.0b2 --with dist/aelix_analytics-0.18.0-py3-none-any.whl   # aelix와 함께
```

- `analytics`는 체크아웃의 `bin/analytics`와 같은 명령·인자·usage(바이트까지 같음)를 쓰고 kit의 출력을 바이트 그대로 넘깁니다
  (`analytics --help`). `bin/analytics`가 셸의 오류에 맡기는 경우(uv가 없거나 시작할 수 없을 때, 문서를 여는 프로그램이 없을 때,
  작업 디렉터리를 읽을 수 없을 때)에는 자기 문구를 냅니다. 종료 코드가 다른 경우를 포함해 측정한 차이는
  [현재 계약](docs/CONTRACTS.md)의 F절에 있습니다. Python 3.11 이상이면 됩니다. 분석은 프로젝트의 uv 환경(Python 3.12 이상)에서 돕니다.
- aelix 환경에 설치하면 aelix가 entry point로 확장을 찾습니다. `aelix extension verify aelix-analytics`가 `BOUND`를
  보여 줍니다. `aelix extension install <wheel>`로도 설치할 수 있습니다.
- aelix와 같은 환경에서 `analytics`도 쓰려면 `uv tool install aelix==0.1.0b2 --with <wheel> --with-executables-from
  aelix-analytics`처럼 설치합니다. `analytics`를 따로 설치하면 kit이 두 벌이 되므로 둘을 함께 업그레이드하세요.
- 설치본을 업그레이드한 뒤에는 aelix를 다시 시작하세요(`/reload`로는 새 코드가 로드되지 않습니다).
- aelix를 다시 설치하면(`install.sh`, `uv tool install --force aelix`) 함께 넣은 이 wheel이 빠집니다. 다시 설치하세요.
- **체크아웃의 symlink와 함께 두지 마세요.** 아래 `~/.aelix/agent/extensions/aelix_analytics.py` symlink가 있으면 aelix는
  두 사본을 모두 로드합니다. 이 버전 이상의 두 사본은 먼저 로드된 하나만 설정되지만, 이전 버전의 체크아웃은 이를
  알지 못해 hook이 두 번 돕니다. 설치본으로 옮길 때 symlink를 지우세요.

## Windows에서 쓸 때

측정한 Windows는 Windows Server 2025의 한 구성입니다(관리자 계정, LongPathsEnabled=1, [현재 계약](docs/CONTRACTS.md#f-실행-환경과-지원-플랫폼) F).
아래 한계는 베타 전에 고치지 않습니다([플랫폼 보강 동결](docs/BETA-QUALITY-GATES.md#베타까지의-플랫폼-보강), 후속 [#38](https://github.com/handochan/aelix-analytics/issues/38)).
항목마다 측정한 구성과 근거는 [현재 계약 G](docs/CONTRACTS.md#g-약속하지-않는-것)의 'Windows의 한계'와
[측정 기록](docs/verification/2026-10-02-issue-8-windows-inventory.md)에 있습니다. (추론)으로 적은 것은 Windows에서 실행하지 않았습니다.

**프로젝트 경로는 짧게 둡니다.** 예: `C:\work\sales`.

- 프로젝트 루트가 MAX_PATH(260자)를 넘으면 aelix 확장이 kit을 시작하지 못합니다. kit을 시작하는 `analytics_run`·`analytics_import`·
  `analytics_query`·`/analytics render`와, 리포트가 아직 없을 때의 `/analytics open`은 `spawn_failed`로 답합니다. `analytics_init`과
  `/analytics init`·`/analytics demo`는 `analytics.json`을 만들고 그 프로젝트를 활성화한 뒤 `spawn_failed`로 답합니다(측정은 kit 0.8.0
  전의 `lab.json`으로 했습니다).
  `analytics_record`·`/analytics record`의 기록은 되지만 리포트는 알리지 않고 다시 만들어지지 않습니다. 측정은 300자 루트에서
  kit 명령의 시작, 환경 동기화, `/analytics init`, `/analytics record`로 했고 나머지는 코드에서 읽었습니다. 실행되지 않을 뿐 기록을
  잃지는 않습니다.
- 이미 그런 루트라면 설치본 `analytics`를 더 짧은 디렉터리에서 부릅니다. 짧은 디렉터리에서 `analytics init <루트>`(디렉터리 인자)로
  그 루트에 프로젝트를 만들었고, 같은 곳에서 `AELIX_ANALYTICS_PROJECT`로 그 루트를 가리킨 `analytics status`가 됐습니다(측정).
  `init`은 이 변수가 아니라 디렉터리 인자로 대상을 받습니다. 다른 명령은 이 방법으로 실행하지 않았습니다.
- LongPathsEnabled가 0이면 Python이 260자를 넘는 경로에 파일을 만들지 못합니다. kit이 Python으로 쓰는 차트 파일(279자)이 그랬고,
  그 run은 `error`로 기록됐습니다(측정). 그런데 그 앞에서 `analytics.save`가 쓴 285자 경로의 데이터셋은 만들어졌습니다(측정).
  `analytics.save`는 데이터셋을 Polars로 쓰고, kit은 상태를 읽지 못한 파일을 말없이 건너뜁니다(코드에서 읽음). 그래서 그 데이터셋은
  run의 출력 목록에도 경고에도 없었습니다(측정). 이렇게 **출력 기록을 잃을 수 있습니다**. run이 `ok`로 끝나도 그런 파일은 출력
  목록에서 빠진다고 봅니다(추론). run이 쓰는 모든 경로가 260자 안에 들게 루트와 스크립트·데이터셋·차트 이름을 짧게 둡니다.
  루트 아래의 프로젝트 환경(`.venv`)의 파일도 200자 안팎의 루트에서는 260자를 넘을 것으로 봅니다(추론, 실행하지 않음: 측정한
  203자 루트의 kit은 체크아웃의 인터프리터로 돌아 프로젝트 환경을 만들지 않았습니다).
- 설정은 PowerShell의 `Get-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem -Name LongPathsEnabled`로 봅니다. 값이
  1이 아니거나, 값이 없어 이 명령이 'does not exist' 오류로 끝나면 꺼져 있습니다. 켜려면 관리자 권한이 필요합니다([Microsoft 문서](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation#enable-long-paths-in-windows-10-version-1607-and-later)).
  이 값은 프로세스마다 처음 한 번 읽히므로 켠 뒤에는 aelix와 터미널을 다시 시작합니다(같은 문서는 재부팅이 필요할 수도 있다고
  합니다). 1에서는 루트 230자의 run이 312자 경로의 출력과 더 깊은 차트 파일까지 기록했습니다(측정). 루트 자체가 MAX_PATH를 넘는
  한계는 1에서도 남습니다.

**run을 멈출 때는 Ctrl+C를 한 번 누르고 결과 줄을 기다립니다.** run은 `cancelled`로 기록되고 `analytics`는 3으로 끝납니다(측정).

- Ctrl+Break는 쓰지 않습니다. uv가 곧바로 끝나 프롬프트가 먼저 돌아오지만 kit은 계속 돕니다. 그동안 그 run의 기록은 `running`이고,
  kit이 프로젝트의 run 잠금을 쥐고 있어 같은 프로젝트의 다음 run은 기다립니다. kit은 나중에 `cancelled`를 기록하고(자는 스크립트는
  잠이 끝난 뒤), 결과 줄은 프롬프트 뒤에 나옵니다(측정). 그 결과 줄이 나올 때까지 콘솔을 닫지 않습니다.
- run이 도는 콘솔 창을 닫지 않습니다. 의사 콘솔을 `ClosePseudoConsole`로 닫은 시험에서 그 run은 `incomplete`로 남았습니다(측정).
  고전 콘솔의 창을 닫거나 사람이 창을 닫는 것은 실행하지 않았지만 같다고 봅니다(추론). 그 기록에는 입력·출력·차트·오류가
  없습니다(코드에서 읽음). 남은 프로세스는 없었고(측정), run 잠금도 함께 풀린다고 봅니다(추론). 기록은 고쳐지지 않으니
  스크립트를 다시 실행합니다.
- Ctrl+C를 거듭 누르지 않습니다. 두 번째 Ctrl+C가 처리기를 다시 설치하는 짧은 순간에 오면 kit이 끝나 run이 `incomplete`로 남을 수
  있습니다(추론). 결과 줄 없이 끝났으면 `analytics status`로 그 run을 봅니다.
- 자식 프로세스를 시작하는 스크립트는 Ctrl+C 뒤에 남은 프로세스를 확인합니다. 자식을 시작하는 바로 그 순간 온 Ctrl+C는 그 자식에
  닿지 않아, run은 `cancelled`로 기록됐지만 자식은 남았습니다(측정, 한 번). 남은 자식이 그 뒤에 쓰는 파일은 기록되지 않습니다(추론).
  남은 프로세스는 이름이 아니라 명령줄로 가려냅니다. aelix와 설치본 `analytics`도 Python으로 돌아 python.exe 프로세스가 있을 수
  있기 때문입니다. PowerShell(Windows PowerShell 5.1과 PowerShell 7)에서
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, ParentProcessId, CommandLine`으로 보고,
  `CommandLine`이 그 스크립트가 시작한 자식의 명령인 프로세스만 `Stop-Process -Id <ProcessId>`로 끝냅니다(측정에서는 자식 하나를
  시작한 스크립트 뒤에 python.exe 둘이 남았습니다). Python이 아닌 자식은 `python.exe` 대신 그 프로그램의 이름으로 찾습니다. aelix나 다른 작업의
  프로세스는 끝내지 않습니다. 이 명령은 Windows에서 실행하지 않았습니다.

**PowerShell로 출력을 받을 때는 인코딩을 먼저 맞춥니다.** `$o = analytics status`처럼 변수에 담으면 PowerShell이 kit의 UTF-8 출력을
콘솔 코드 페이지로 읽어 한국어가 깨집니다(pwsh 7.6.6, 코드 페이지 437·949에서 측정; Windows PowerShell 5.1은 실행하지 않음).
`ForEach-Object` 같은 cmdlet에 파이프로 넘겨 문자열로 받아도 같다고 봅니다(추론). PowerShell 7.4부터 외부 프로그램끼리의 파이프와
`>` 리디렉션은 바이트를 그대로 넘깁니다([PowerShell 문서](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_redirection?view=powershell-7.6#example-7-redirecting-binary-data-from-a-native-command)).
콘솔에 바로 보이는 출력은 바릅니다. 같은 세션에서 먼저 다음을 실행합니다(추론, Windows에서는 실행하지 않음):

```powershell
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
```

이 줄은 PowerShell이 받은 바이트를 읽는 방식과 그 콘솔의 출력 코드 페이지만 바꿉니다. kit이 쓰는 바이트와 기록은 그대로 UTF-8입니다.
PowerShell 안에서 실행한 `chcp 65001`은 PowerShell이 이미 읽어 둔 인코딩을 바꾸지 않아 효과가 없을 수 있습니다.

**aelix의 `analytics_run`은 환경 변수를 모두 넘기지 않습니다.** Windows에서 확장은 시스템의 기본 변수와 허용 목록만 넘기므로
`USERNAME`·`HOMEDRIVE`·`HOMEPATH`·사용자가 둔 `PYTHONUTF8` 등이 빠집니다. 그래서 `getpass.getuser()`를 부르는 스크립트는
`analytics_run`에서 실패할 수 있습니다. 셸의 `analytics run`은 환경을 그대로 넘기므로 될 수 있습니다(추론, 실행하지 않음). 우회:

- aelix를 시작하기 전에 `$env:USER = $env:USERNAME`(PowerShell)을 둡니다. 확장은 `USER`를 넘기고, `getpass.getuser()`는
  `USERNAME`보다 `USER`를 먼저 읽습니다.
- `PYTHONUTF8`에 기대지 말고 텍스트 파일을 열 때 `encoding="utf-8"`을 적습니다. 적지 않으면 오류 없이 로캘의 인코딩으로 다른
  글자를 읽고 run이 `ok`로 끝날 수 있습니다(추론).

## 확장 등록

| 방법 | 명령 | 언제 |
|---|---|---|
| 설치본 | 위 "설치본으로 쓰기" | 체크아웃 없이, Windows |
| 세션 단위 | `aelix -e $PACK/extension/aelix_analytics.py [--analytics-project DIR]` | 시험·비교할 때 |
| 전역 등록 | `mkdir -p ~/.aelix/agent/extensions && ln -s $PACK/extension/aelix_analytics.py ~/.aelix/agent/extensions/aelix_analytics.py` | 계속 쓸 때 (모든 aelix 세션에 도구 6개가 붙음) |
| 해제 | `rm ~/.aelix/agent/extensions/aelix_analytics.py` | |

- **복사하지 말고 symlink** 하세요. 확장 파일이 체크아웃의 `src/`(kit, package `aelix_analytics`가 있는 곳)를 찾습니다. 복사했다면
  `AELIX_ANALYTICS_RUNTIME_PATH=$PACK/src`이고, 체크아웃을 업데이트할 때마다 다시 복사해야 합니다(kit 0.2.0 전의 `$PACK/kit`은 이제
  "kit not found"로 실패합니다).
- `aelix status`로 로드 여부를 확인할 수 있습니다.
- **디렉터리로 자동 활성화되는 프로젝트는 "내가 만들었거나 고른 것"뿐입니다.** `analytics init`·`analytics demo`·`analytics migrate`·
  `/analytics use`가 프로젝트 경로를 신뢰 목록 `~/.aelix/agent/analytics-projects.json`(`analytics.trust/1`, kit 0.8.0)에 적고, 그 목록에
  있는 프로젝트 안에서 aelix를 실행할 때만 활성화됩니다. kit 0.8.0 전의 목록 `lab-projects.json`은 고치지 않고 `lab.json` 프로젝트의
  활성화에만 읽습니다. 남에게 받은 사본은 `/analytics use DIR` 한 번이 필요합니다(그 CONTEXT.md가 프롬프트에 들어가고
  `analytics_run`이 그 코드를 실행하기 때문입니다).
- Aelix Analytics Engine 어댑터와 이름이 겹치지 않습니다
  (`analytics_*`·`/analytics` vs `analytics_engine_*`·`/analytics-engine`).
  다만 비교 평가는 한 번에 하나만 로드해야 공정합니다.

프로젝트는 `/analytics use` → `--analytics-project` → `AELIX_ANALYTICS_PROJECT` → 작업 디렉터리와 그 조상 가운데 표식(`analytics.json`
또는 `lab.json`)이 있는 가장 가까운 디렉터리 순서로 찾습니다. kit 0.8.0부터 이 kit은 `analytics.json`(`analytics.project/2`) 프로젝트에만
쓰고, `lab.json` 프로젝트는 읽기만 합니다: 쓰기는 `migration_required`와 전환하는 법을 알리는 안내로 거절하고, 맥락 동기화·로그 같은 부수
쓰기는 건너뜁니다. 모르는 형식·깨진 `analytics.json`·두 표식의 디렉터리에도 기록·잠금 파일·캐시·로그를 쓰지 않습니다(읽기는 두 표식만
거절). 다만 `analytics`·`bin/analytics`·`analytics_query`가 그 프로젝트의 환경으로 kit을 부르면 uv가 `.venv/`·`uv.lock`을 만들거나 고칠 수
있습니다(기록이 아닌 환경, [현재 계약](docs/CONTRACTS.md) B). 다른 프로젝트 안과 끝나지 않은 전환의 목적지 안에는 새 프로젝트를 만들지
않습니다(`destination_invalid`).
가장 쉬운 방법은 프로젝트 디렉터리에서 aelix를 실행하는 것입니다. `AELIX_ANALYTICS_PROJECT`는 셸의 `analytics`(`bin/analytics`)도
작업 디렉터리보다 먼저 읽습니다: aelix를 위해 설정해 두면 다른 프로젝트 안에서 실행한 `analytics record`도 그 프로젝트에 기록됩니다.

## aelix에서의 흐름

1. 프로젝트 만들기: `/analytics init ~/analyses/q3-sales 3분기 매출` — 또는 모델에게 위치를 알려 주고 만들어 달라고 합니다(`analytics_init`).
2. `CONTEXT.md`에 목표·핵심 질문·데이터 출처·정의를 채웁니다(사람이 직접 또는 모델에게 요청).
3. "이 CSV를 가져와서 월별 추세를 봐줘" → 모델이 `analytics_import` → `analytics_query`로 탐색 → `scripts/01_*.py` 작성 → `analytics_run`.
4. `/analytics open`으로 리포트를 열어 둡니다. run·import·메모가 생기면 **자동 새로고침**됩니다.
5. 확정된 내용은 `analytics_record`(finding·decision·assumption·question·todo), 막히거나 우회가 필요했던 점은 `/analytics record workflow_feedback …`.

### 모델 도구

| 도구 | 하는 일 | 쓰기 |
|---|---|---|
| `analytics_status` | 데이터셋·view 이름, 최근 run, 차트 최신 버전과 stale 여부, 열린 질문, 핵심 정보, 최근 journal | 없음 |
| `analytics_query` | 데이터셋 view 위의 읽기 전용 DuckDB SQL 한 문장(SELECT/WITH/DESCRIBE/SUMMARIZE/EXPLAIN). 쓰기 문장과 `EXPLAIN ANALYZE`는 거절하고, 파일 접근을 `data/`로 제한합니다 | `.analytics/queries.jsonl` 로그만 |
| `analytics_import` | CSV/TSV/JSON/NDJSON/Parquet → `data/imported/<name>.parquet`(불변), 원본 경로·sha256·스키마 기록 | 예 |
| `analytics_run` | 스크립트 실행 + 코드 스냅샷·입력 해시·출력·차트·플롯 데이터·로그·환경 기록, 리포트 재생성. `rerun_of`로 재실행 비교 | 예 |
| `analytics_record` | journal 기록(이전 `lab_note`): finding·decision·assumption·question·todo·note·resolve·workflow_feedback. `resolves`로 이전 질문·할 일을 닫고, 본문은 32 KiB까지 자르지 않음 | 예 |
| `analytics_init` | 새 프로젝트 + uv 환경 (사용자가 요청했을 때만) | 예 |

PNG 차트는 모델이 이미지 입력을 받을 때 `analytics_run` 결과에 이미지로도 붙습니다(모델이 자기 차트를 봅니다).

### 사람 명령

```text
/analytics                  현황              /analytics render | open      리포트 재생성 | 브라우저로 열기
/analytics where            프로젝트·kit·uv   /analytics record KIND TEXT   사람 이름으로 journal 기록
/analytics init DIR [제목]  새 프로젝트       /analytics record workflow_feedback TEXT   사용 중 부족하거나 어색했던 점 기록
/analytics demo DIR         데모 프로젝트     /analytics context            프롬프트에 주입되는 맥락 블록 보기
/analytics use DIR          활성 프로젝트 지정
```

셸에서는 같은 기능을 체크아웃의 `bin/analytics`나 설치본의 `analytics`로 씁니다(`analytics status`, `analytics run scripts/x.py --title …`,
`analytics query "SQL"`, `analytics record finding "…"`, `analytics render` 등). 옛 형식 프로젝트의 전환(`analytics migrate`)은 셸에서만
부릅니다(아래).

### 작성 주체

기록에는 누가 썼는지(`actor`)와 그 근거(`actor_basis`), 실행 수단(`means`)이 남습니다. aelix 도구는 `agent`, `/analytics`는 `human`이고,
셸의 `analytics`는 `AELIX_ANALYTICS_ACTOR`의 값(밝힌 것, `declared`)이나 없으면 `human`(`inferred`)입니다. aelix 안에서는 확장이 aelix
프로세스의 환경에 `AELIX_ANALYTICS_ACTOR=agent`와 세션을 넣으므로, 에이전트가 bash 도구로 부른 `analytics`는 `agent`로, 사람이 `!analytics …`로
부른 것은 `human`으로 기록됩니다(aelix가 그 줄을 돌릴 셸 — POSIX 셸이나 PowerShell — 로 읽어 줄 전체가 kit 명령 하나일 때. `cd proj &&
analytics status`처럼 다른 명령과 합친 줄, `$HOME`·`$'…'`처럼 셸이 펼치는 `$`가 든 줄, cmd·fish 같은 다른 셸의 줄, Windows에서 cmd가 읽는
글자(`&`·`|`·`%`·`"` 등)를 따옴표로 담은 줄(`'a & b'`; 이름이 배치 파일로 풀리면 cmd가 인자를 다시 읽음), 셸이 단어 여럿으로 펼치는
패턴·중괄호가 따옴표 밖에 든 줄(`*.csv`·`{a,b}`), PowerShell이 단어를 POSIX 셸과 달리 나누는 줄(`'a'b`·`-p3.12`·`a,b`·`@x`), `uv run -m aelix_analytics`·`sudo analytics`·PowerShell의 모든 스트림 리디렉션(`*> log.txt`·`*>&1`)·끝의
`&`·`;`처럼 판정이 받지 않는 형태의 kit 기록은 `agent`입니다). aelix 밖의 에이전트(다른 코딩 에이전트, 자동화)가 `analytics`나 SDK를 부르게 한다면 그 에이전트의 셸에
`AELIX_ANALYTICS_ACTOR=agent`를 설정하세요. 설정하지 않으면 사람의 기록(`human`·`inferred`)으로 남습니다. 출처 기록이지 인증이 아닙니다.
한계: aelix 0.1.0b2는 `!` 명령에 대한 확장들의 답 가운데 마지막 것 하나만 씁니다. 그래서 `!` 명령을 막거나 다른 실행기(샌드박스·원격
실행 등)로 돌리는 확장이 이 확장보다 먼저 등록되면, **줄 전체가 kit 명령 하나인 `!` 명령에 한해 이 확장이 그 확장의 막음이나 실행기를
덮어 aelix의 로컬 셸로 돌립니다**(막힌 kit 명령도 돌아 기록을 씁니다). `;`·`&&`·`|` 등으로 다른 명령과 합친 줄, 판정이 열어 두는 줄과
kit을 부르지 않는 줄은 그 확장의 답대로입니다. 소유자가 덮는 범위를 그 kit 명령 하나로 줄여 처리기를 두기로 했습니다(2026-10-09). 판정 규칙과 순서별 동작은
[현재 계약](docs/CONTRACTS.md) A "aelix 안의 작성 주체"에 있습니다.

### 스크립트 API

```python
import aelix_analytics as analytics

sales = analytics.read("sales")                                    # Polars DataFrame (data/imported/sales.parquet)
monthly = analytics.sql("SELECT ... FROM sales GROUP BY ALL").pl() # 모든 데이터셋이 view인 DuckDB
analytics.save(monthly, "monthly_revenue")                         # data/derived/monthly_revenue.parquet
analytics.visualize(fig, "monthly-revenue", title="…", caption="…", data=monthly)   # matplotlib/plotly
analytics.record("finding", "…")                                       # 이 run에 연결된 journal 기록
```

run을 시작한 명령(`analytics`, `bin/analytics`, aelix 도구)이 이 package를 run에 줍니다. kit 0.2.0 전의 이름 `import labkit as lab`은
더는 import되지 않고, kit 0.3.0 전의 `analytics.note`·`analytics.chart`는 `analytics.record`·`analytics.visualize`가 되었습니다(인자 같음,
기록되는 내용 같음). 옛 이름을 쓴 스크립트는 새 이름을 알려 주는 오류로 실패하니 고치세요. 옛 kit으로 기록한 run을
그대로 다시 실행하려면 그 kit이 필요합니다. 설치본의 `python -m aelix_analytics`는 거절되므로 셸에서는 `analytics`를 씁니다.

SDK를 쓰지 않은 **관용 코드도 기록됩니다**: `pl.read_parquet("data/…")`, `duckdb.sql("… FROM 'data/….parquet'")`,
`plt.show()`, plotly `fig.show()`. 차트의 **명시적인 첨부 표**는 `analytics.visualize(..., data=df)`로 남깁니다.
첨부 표가 요약 통계나 일부 계열이면 그림 전체를 재구성하는 데 입력·코드도 필요하며, 첨부 자체가 완전한 플롯 대응을 보장하지 않습니다.
matplotlib에는 한글 폰트와 검증된 기본 팔레트가 적용됩니다(`AELIX_ANALYTICS_STYLE=0`으로 끔).

### 분석 자산과 선택적 제안

`analytics assets list/show ID`와 SDK `assets()`는 후보의 버전·검사·효과를 읽습니다. `assets review [ID]`와
`review_assets()`는 명시적 요청이며 모델 없이 판정하고 요청 이력을 남깁니다. 검증한 후보의 등록은 `assets register EVENT --name NAME`으로
명시적으로 선택합니다. 프로젝트의 파일만 복사하며 등록·발견·실행은 별개입니다.

기본 제안 모드는 on_demand입니다. 사람이 `analytics assets mode always_on` 또는 SDK `set_asset_mode("always_on")`을 실행하면
일반 사람 텍스트 상태(`analytics --text status`)에서 확정 근거·점수·거절 사유·중복과 최소 24시간 프로젝트 창을 확인해 한 후보를 제안합니다.
`analytics assets mode on_demand`로 끕니다. 추가 모델 호출은 없고, 끄더라도 이미 기록한 미응답 제안은 pending으로 조회할 수 있습니다.
JSON·agent-origin CLI·SDK status와 모든 records/relations/context 상세 페이지는 자동 발행하지 않습니다.
설정 오류와 과거 설정 키는 상태에 표시하며 자동 활성화하지 않습니다.
점수·이력·빈도·실패의 자세한 규칙은 [현재 계약 C8](docs/CONTRACTS.md#c8-프로젝트-모드와-결정적-자동-제안-kit-0120-17-b3-b)에 있습니다.

Aelix에서는 사람 명령 `/analytics assets mode always_on --scope session`으로 현재 세션만 켤 수 있습니다.
같은 세션 파일을 reload/resume하면 선택이 유지되고 new/fork은 프로젝트 모드를 따릅니다. `on_demand`로 끌 수 있습니다.
`/analytics assets list|show|review|update|register`는 같은 자산 응용 기능을 쓰며 등록은 명시적인 프로젝트 파일 복사입니다.
상시 모드의 다음 턴 맥락과 일반 사람 `/analytics status`는 CLI와 같은 프로젝트 창을 사용합니다. AssistantDone는 사전 판정만 하고
모델·새 턴·UI 알림을 시작하지 않습니다. 기본 요청 시 모드는 후보 본문이나 검토 지침을 맥락에 추가하지 않습니다.
Workspace의 **Analysis assets**는 저장된 후보 버전·판정 snapshot·미응답 알림과 정식 기록 링크를 읽습니다.
세션·표면·파생 cache의 자세한 규칙은 [현재 계약 C9](docs/CONTRACTS.md#c9-aelix-세션맥락과-workspace-분석-자산-kit-0130-17-b3-c)에 있습니다.

### 옛 이름에서 옮기기

kit 0.3.0(#9)에서 공개 이름이 `analytics`로 바뀌었습니다. 옛 이름은 별칭 없이 새 이름을 알려 주는 오류로 멈춥니다.

| 옛 이름 | 새 이름 |
|---|---|
| `bin/lab`, 하위 명령 `note`·`report` | `bin/analytics`(설치본 `analytics`), `record`·`render` |
| `/lab`(`note`·`gap`·`report`) | `/analytics`(`record KIND TEXT`·`record workflow_feedback TEXT`(kit 0.8.0 전 `record gap TEXT`)·`render`) |
| 도구 `lab_status`·`lab_query`·`lab_import`·`lab_run`·`lab_note`·`lab_init` | `analytics_status`·`analytics_query`·`analytics_import`·`analytics_run`·`analytics_record`·`analytics_init` |
| flag `--lab-project` | `--analytics-project` |
| `extension/aelix_lab.py` | `extension/aelix_analytics.py`(전역 symlink를 다시 만드세요) |
| SDK `analytics.note`·`analytics.chart` | `analytics.record`·`analytics.visualize` |
| `LAB_PROJECT`·`AELIX_LAB_PROJECT` | `AELIX_ANALYTICS_PROJECT`(하나로 합침) |
| `LAB_ACTOR`·`LAB_SESSION`·`LAB_STYLE` | `AELIX_ANALYTICS_ACTOR`·`AELIX_ANALYTICS_SESSION`·`AELIX_ANALYTICS_STYLE` |
| `AELIX_LAB_KIT`·`AELIX_LAB_UV`·`AELIX_LAB_TRUST_FILE` | `AELIX_ANALYTICS_RUNTIME_PATH`·`AELIX_ANALYTICS_UV`·`AELIX_ANALYTICS_TRUST_FILE` |

- 옛 환경 변수는 그것을 읽던 쪽이 (새 이름과 함께라도) 오류로 거절합니다. `analytics`·`bin/analytics`는 `LAB_PROJECT`·`LAB_ACTOR`·
  `AELIX_LAB_UV`에, 그것이 부르는 kit은 `LAB_PROJECT`·`LAB_ACTOR`·`LAB_SESSION`·`LAB_STYLE`·`AELIX_LAB_TRUST_FILE`에 아무것도 쓰기
  전에 멈추고, aelix 확장은 `AELIX_LAB_PROJECT`·`AELIX_LAB_UV`·`AELIX_LAB_TRUST_FILE`(경로로 로드한 확장 파일은 `AELIX_LAB_KIT`도)에
  로드 오류로 보입니다. 읽은 적 없는 이름은 보지 않습니다(예: `analytics`와 kit은 `AELIX_LAB_PROJECT`를, 확장은 `LAB_PROJECT`를).
  자세한 것은 [현재 계약](docs/CONTRACTS.md) A·D에 있습니다.
- 옛 flag `--lab-project`를 준 aelix도 확장을 로드하지 않습니다(aelix는 모르는 flag를 말없이 넘기므로, 무시하면 다른 프로젝트에
  기록될 수 있습니다).
- 옛 이름을 쓴 run 스냅샷은 그때의 kit으로만 그대로 다시 실행됩니다.
- 이전 세션의 telemetry에는 `lab_*` 도구 이름이 남아 있고, 리포트의 도구 사용표에는 옛 이름과 새 이름이 각각 보입니다.

kit 0.8.0(#13)에서 저장 이름과 기록 형식이 바뀌었습니다. 새 프로젝트는 처음부터 새 이름이고, 그 전의 `lab.json` 프로젝트는 이 kit이
읽기만 합니다(아래 "옛 형식 프로젝트 옮기기"). 기존 기록의 바이트는 고치지 않습니다.

| kit 0.8.0 전 | kit 0.8.0 |
|---|---|
| 표식 `lab.json`(`lab.project/1`) | `analytics.json`(`analytics.project/2`, `project_id`·`min_writer_version`) |
| 관리 디렉터리 `.lab/` | `.analytics/` |
| Workspace `report/` | `workspace/` |
| 가져온 데이터 `data/raw/<name>.parquet`·`.meta.json` | `data/imported/<name>.parquet`·`.meta.json`(`analytics.dataset/1`) |
| run의 차트 `runs/<id>/charts/`, `charts[]` | `runs/<id>/visualizations/`, `visualizations[]`(`analytics.run/2`) |
| journal 줄 `lab.event/1` | `analytics.event/2`(`actor_basis`·`means`·`request_id`, 쓰는 때 결박한 참조) |
| kind `gap` | `workflow_feedback`(입력 `gap`은 `bad_kind`) |
| 기록의 `via` `lab.read`·`lab.chart` 등 | `analytics.read`·`analytics.visualize` 등 |
| 신뢰 목록 `lab-projects.json` | `analytics-projects.json`(`analytics.trust/1`) |

### 옛 형식 프로젝트 옮기기 (`analytics migrate`)

kit 0.8.0은 `lab.json` 프로젝트에 쓰지 않습니다. 이어서 쓰려면 셸에서 새 빈 폴더로 복사합니다:

```sh
cd ~/analyses/q3-sales                      # lab.json 프로젝트 (또는 AELIX_ANALYTICS_PROJECT)
analytics migrate --to ~/analyses/q3-sales-v2 --dry-run   # 먼저 미리 보기: 아무것도 쓰지 않고 계획과 거절 이유를 보임
analytics migrate --to ~/analyses/q3-sales-v2             # 복사 (원본은 그대로)
```

- **원본은 바뀌지 않습니다.** 옛 kit의 잠금 파일 둘 말고는 원본에 쓰지 않고, 목적지에만 씁니다. 목적지는 없거나 빈 폴더여야 하고 원본
  안·원본을 품은 위·다른 프로젝트 안·끝나지 않은 다른 전환의 목적지 안이면 거절합니다(`destination_invalid`). 미리 보기가 대상이 없는 바깥
  링크, Linux의 UTF-8이 아닌 이름 같은 거절을 먼저 알립니다.
- `.venv/`·`report/`·캐시·잠금 파일과 운영체제가 만드는 파일(`.DS_Store`·`Thumbs.db`·`desktop.ini`, macOS가 FAT·exFAT 같은 볼륨에 만드는
  `._<이름>` 파일)은 복사하지 않고 보고서에 적습니다. 목적지에 `.DS_Store`나 `._<이름>` 파일만 있으면 빈 폴더로 봅니다. `._`로 시작하는
  이름이어도 폴더나 링크는 보통 항목입니다(복사하고, 목적지에 있으면 빈 폴더가 아님).
- 끊기면 같은 명령을 다시 부르면 이어서 끝냅니다. 끝난 전환을 다시 부르면 `already_migrated`와 함께 원본과 사본을 다시 대조합니다.
  끝나기 전의 목적지에서는 모든 명령이 `migration_incomplete`로 멈춥니다.
- 커밋한 뒤 `analytics`는 kit이 답한 목적지(`--to=~/x`처럼 셸이 풀지 않은 `~`도 kit이 풀어 만든 폴더)의 환경을 `uv sync --locked`로
  준비합니다. 실패해도 전환은 끝났고 다음 명령이 환경을 준비합니다.
- 목적지는 신뢰 목록에 더해집니다. aelix에서는 `/analytics use <목적지>`로 이어 갑니다. 에이전트도 이 명령을 부를 수 있고, 그때는 보고서와
  응답에 `agent`로 남습니다. aelix의 도구·`/analytics`에는 전환 명령이 없습니다.
- 목적지의 `.analytics/migration/<id>/`에 계획·ledger·보고서·경로 대응(매핑)이 남고, 옛 run이 기록한 경로는 그 매핑으로 읽습니다.
  링크가 없는 파일 시스템(FAT·exFAT 등)에서는 표식을 "있으면 실패하는 rename"으로 게시하고, 그것도 없으면 복사 전에
  `destination_invalid`(`no_exclusive_rename`)로 거절합니다. 자세한 규칙과 한계는 [현재 계약](docs/CONTRACTS.md) B "프로젝트 전환"·G에 있습니다.

## 프로젝트 구조

```text
my-analysis/
  analytics.json           프로젝트 정체성 (analytics.project/2: project_id·title·created_at·min_writer_version)
  CONTEXT.md               지금 유효한 맥락: 목표·질문·데이터 출처·정의·가정·결론·다음 할 일
  journal.jsonl            append-only 타임라인 (analytics.event/2): project·import·run·context·finding·decision·assumption·question·todo·note·workflow_feedback·resolve
  pyproject.toml, uv.lock  이 프로젝트만의 환경 (DuckDB·Polars·PyArrow·matplotlib·plotly)
  data/imported/<name>.parquet  가져온 데이터(불변 규약) + <name>.meta.json (analytics.dataset/1: 원본 경로·sha256·스키마)
  data/derived/            run이 만든 Parquet
  scripts/                 편집하는 분석 코드
  runs/<YYYYMMDD-HHMMSS-script>/
    run.json               기록 (analytics.run/2): 코드 sha·입력 sha·출력·Visualization·오류·환경·재실행 비교
    code/                  실행된 코드 스냅샷 (+ import된 로컬 모듈)
    visualizations/        Visualization 파일 + <id>.data.parquet (플롯 데이터)
    stdout.txt, stderr.txt
  workspace/               생성된 HTML (index·runs/·data/·context.html) — 직접 편집하지 않음
  .analytics/              context 버전, telemetry, query 로그, 캐시, 잠금, 전환 기록(migration/)
  .gitignore, .gitattributes   git 설정 (.gitattributes는 kit 0.4.0부터 새 프로젝트에)
```

kit 0.8.0 전의 `lab.json` 프로젝트는 `lab.json`·`.lab/`·`report/`·`data/raw/`·`runs/<id>/charts/`(`lab.*/1`)이고, 이 kit은 그 배치를 그대로
읽습니다. 관리 디렉터리는 표식의 배치마다입니다: 새 프로젝트의 `.lab/`·`report/`는 사람의 폴더라 run이 거기 쓴 파일은 출력으로 기록됩니다.

필드 정의는 [docs/CONTRACTS.md](docs/CONTRACTS.md)에 있습니다.

kit이 쓰는 텍스트(기록·템플릿 사본·inline 스크립트·Workspace·run 로그)는 모든 OS에서 UTF-8·LF이고, 사용자 파일은 바이트 그대로
둡니다([ADR-0007](docs/decisions/0007-line-endings.md)). 새 프로젝트의 `.gitattributes`(`* -text`)는 git이 이 프로젝트 파일의 줄 끝을
바꾸지 않게 합니다. 기록이 입력·스크립트의 바이트를 해시하므로, git이 LF와 CRLF를 바꾸면 다른 기계에서 `changed`로 보이기
때문입니다. 다른 git 저장소 안의 하위 디렉터리에 만든 프로젝트에서는 그 하위 트리에 한해 상위 저장소의 줄 끝 규칙(예:
`* text=auto eol=lf`)을 덮습니다. 원하지 않으면 그 파일을 지우세요(그러면 git이 `core.autocrlf` 설정이나 상위 저장소의
규칙대로 줄 끝을 다룹니다). 줄 끝만 바뀐 입력·스크립트는 상태는 `changed` 그대로이고 사유 `line endings only`가 함께 보입니다.

## 차트에서 데이터·코드까지

- **기록 단위는 run입니다.** 차트는 반드시 한 run에 속하고, run은 실행한 바이트 그대로의 코드 스냅샷을 가집니다.
  나중에 스크립트를 고쳐도 차트가 가리키는 코드는 바뀌지 않습니다.
- **입력 감지(최선 노력)**: 런타임 훅(Polars 읽기, DuckDB 읽기·SQL 파일 리터럴, `analytics.read/scan/sql`),
  코드의 경로 리터럴 정적 스캔, 호출 시 선언(`inputs`), 이 셋을 합칩니다. 각 입력에 어느 경로로 감지됐는지(`via`)가 남습니다.
  **출력**은 run 전후 프로젝트 파일 비교로 빠짐없이 잡습니다.
- **stale**: 차트를 만든 run의 입력 파일 sha256이 지금과 다르거나, 그 입력과 같은 바이트를 출력한 성공 run(그 run보다 늦게 시작하지 않은 것,
  생산자 후보) 가운데 stale인 것이 있으면(전이) 표시됩니다. 후보가 여럿이면 하나를 고르지 않고 모두 따라가며 `producer ambiguous`라고 적습니다.
  후보는 같은 바이트를 출력했다는 관측이라, stale인 후보는 생산자로 단정하지 않고 `upstream candidate`로 적습니다.
- **관계의 상태**: `analytics_status`와 리포트는 기록을 한 번 읽어 참조마다 상태를 보입니다(ADR-0006): run 이벤트의 차트는 그 run의 차트로, 버전을
  고정하지 않은 차트·데이터는 `Version not named`와 최신 버전 링크("latest, not the cited version")로, 없는 대상은 `Not found when cited`·`File missing now`처럼 그 이유로.
  두 화면은 같은 식별자와 읽기 기준(`as_of_seq`·`source_revision`)을 적습니다.
- **버전**: `스크립트::차트id`마다 최신 성공 run이 현재 버전이고, 이전 버전은 카드와 run 페이지에서 따라갈 수 있습니다.
- **재실행 검증**: `analytics_run(rerun_of=…)`은 원래 스냅샷을 다시 실행해 플롯 데이터 프레임 동등성, 출력 바이트를 비교하고
  `match / mismatch / failed / inconclusive`를 기록합니다. 재실행이 실패했거나, 차트·출력이 사라지거나 새로 생기면
  `match`가 되지 않습니다. 재실행 run은 검증 기록이라 차트의 현재 버전이 되지 않습니다.

## 맥락 · 중요 정보 · 시간 흐름

- `CONTEXT.md`: 사람과 모델이 함께 고치는 현재 맥락. 바뀔 때마다 `.analytics/context/`에 버전이 남고 journal에 `context` 이벤트가
  생기며, Workspace의 Context history 페이지에서 diff로 봅니다(run·import·note·프롬프트 시작·에이전트 턴 종료 때 감지).
  새 프로젝트의 템플릿은 영어 섹션(Goal·Analysis focus·Next steps·Current findings·Definitions and rules·Data sources·
  Assumptions and limitations)이고, 내용은 어느 언어로 써도 됩니다. 옛 템플릿의 `## 목표`·`<!-- 항상 -->`도 그대로 읽습니다.
- `journal.jsonl`: 모든 사건의 시간순 기록. 발견·결정·가정은 **핵심 정보**, 질문·할 일은 **열린 항목**이며,
  나중 기록의 `resolves`로 닫힙니다. run·이벤트에는 aelix 세션 파일 경로가 함께 남습니다.
- **프롬프트 주입 — 섹션 단위 점진적 공개**: 매 요청 전에 `CONTEXT.md` + 해결되지 않은 핵심 정보 + 열린 항목을
  시스템 프롬프트 뒤에 붙입니다. CONTEXT.md는 통째로 넣지 않고 **`##` 섹션 단위**로 넣습니다 —
  제목에 `<!-- pin -->`(옛 템플릿의 `<!-- 항상 -->`, `<!-- always -->`도)을 붙인 섹션과, 앞에서부터 예산(4,000자)이 허락하는 섹션이 본문째 들어가고,
  **나머지는 제목·줄 범위만** 들어갑니다(`read CONTEXT.md offset N limit M`). 줄 번호는 aelix의 read 도구가
  세는 그대로입니다(`\n`으로만 나누고, 앞의 빈 줄과 CRLF 파일도 같게). 문장 중간에서 잘리는 일이 없고,
  무엇이 빠졌는지는 `analytics_status`와 Workspace의 Context 탭이 이름으로 알려 줍니다. 순서를 바꾸거나 `<!-- pin -->`을
  붙이는 것이 통제 수단입니다. 자주 바뀌는 현황은 `analytics_status`로 봅니다.
- **빠진 것과 다시 읽을 위치**: 첫 `##` 위의 머리말이 4,000자를 넘으면 앞 4,000자만 들어가고, 뺀 글자 수와 머리말 전체의 줄 범위
  (`read CONTEXT.md offset 1 limit N`)가 붙습니다. 핵심 정보와 열린 항목은 각각 최근 12개가 들어가고(글은 300자까지),
  더 있거나 잘린 글이 있으면 몇 개를 뺐는지와 `journal.jsonl` 경로를 적습니다. `analytics_status`·`analytics_run`·`analytics_query` 등의
  응답도 줄이거나 자른 자리에 개수와 위치(journal, run의 `run.json`과 로그 파일, 데이터셋의 `meta.json`)를 적습니다.
  위치는 텍스트를 만든 때의 파일을 가리키므로, 그 뒤에 CONTEXT.md를 고치면 줄 범위가 어긋날 수 있습니다.
  자세한 것은 [계약 A1](docs/CONTRACTS.md#a1-모델에-가는-텍스트의-생략과-다시-읽을-위치)입니다.

현재 4,000자는 일반 섹션 선택의 기준이며 **전체 프롬프트 주입량의 상한은 아닙니다**. 고정 섹션은 이를 넘길 수 있고,
생략 섹션 목록과 journal 요약도 추가됩니다. 공식 베타 설계는 전체 사용량과 분석 품질을 함께 측정하며,
토큰 목표를 강제 제한으로 사용하지 않습니다.

## 기록 상태와 사용 중 문제 — 현재 인터페이스

- 쓰다가 부족한 점은 바로 `/analytics record workflow_feedback <내용>`. 모델도 막히면 `analytics_record(kind="workflow_feedback")`을 남기도록
  지침이 들어 있습니다(kit 0.8.0 전의 이름 `gap`은 입력에서 `bad_kind`이고, 옛 기록의 `gap`은 그대로 읽습니다).
- 확장이 **모든 도구 호출**을 `.analytics/telemetry.jsonl`에 남기고, 규약 우회 의심(`python_via_bash`, `wrote_managed_path`)을 표시합니다.
- Workspace의 **Evaluation** 탭(옛 '평가' 탭): Workflow feedback(`workflow_feedback`·옛 `gap` 목록), Record coverage(입력 미감지 run, 첨부 표 없는 차트,
  프로젝트 밖 입력, 입력이 바뀐 차트, 재실행 비교 결과), Tool usage(도구별 호출·오류·시간), Possible workarounds(우회 의심 호출).
- 기존 비교 실험의 축과 시나리오: [과거 평가 가이드](docs/EVALUATION.md).

공식 베타에서는 이 탭을 **인사이트**로 전환해 분석 발견과 재사용 제안을 연결하고, 기록 품질·도구 사용은
진단 정보로 유지할 계획입니다. 기존 workflow feedback은 사용 중 문제로 보존합니다. [설계](docs/DESIGN-CONTINUITY-AND-REUSE.md)를 참고하세요.

## 한계

- **샌드박스·승인 없음.** 스크립트는 사용자 권한으로 돌고, aelix는 확장 도구 실행 전에 묻지 않습니다(헤드리스 모드도 자동 승인).
- **계보는 최선 노력.** 실행 중 계산된 경로를 C 확장이 직접 여는 경우, 훅이 없는 API(예: pyarrow 직접 읽기)는 놓칠 수 있습니다.
  경고(`no input files were detected`)를 보고 `inputs`로 선언하세요. 행 단위 계보는 없습니다.
- **불변성은 규약입니다.** `data/imported`·`runs`를 사람이 고치는 것을 막지 않습니다. 해시 비교로 드러날 뿐입니다. 해시 체인·서명 없음.
- **트랜잭션·복구 없음.** 강제 종료된 run은 `incomplete`로 보입니다. 같은 요청을 두 번 보내면 run이 두 개 생깁니다(멱등 키 없음).
  한 프로젝트의 run은 파일 잠금으로 직렬화되므로(출력은 실행 전후 파일 비교로 찾습니다) 동시 요청은 순서를 기다립니다.
  기록 중 일부 단계가 실패하면 run.json의 `finalize_errors`와 run 페이지에 남습니다.
- **링크와 철자.** 기록의 경로는 프로젝트의 이름 공간 경로입니다: `data/`가 다른 디스크로 가는 링크여도 `data/…`이고, 철자는 파일
  시스템에 저장된 이름입니다(kit 0.5.0; kit이 관리하는 `runs/`·`.venv/`·`.analytics/`·`workspace/`(`lab.json` 프로젝트는 `.lab/`·`report/`)는
  저장된 대소문자와 관계없이 그 이름).
  링크 대상의 절대 경로로 직접 읽은 파일은 프로젝트 밖 경로이고, `analytics_query`는 `data/` 안에서 밖으로 가는 링크 아래를 읽지
  않으며, run이 만든 링크 아래의 파일은 스크립트가 시작한 뒤 수정된 것만 출력인 등의 한계는 [현재 계약](docs/CONTRACTS.md) G의
  "경로 정체성의 한계"에 있습니다.
- **재실행은 같은 환경 가정.** 환경 지문 비교가 없고, 로컬 helper 모듈은 스냅샷이 아니라 현재 `scripts/`에서 import됩니다.
- **규모.** 리포트는 매번 전체를 다시 만들고, 데이터셋 프로파일은 전체를 스캔합니다(sha 기준 캐시). 수백 run·수 GB에서는 느려집니다.
  plotly 그림은 JSON째 페이지에 들어갑니다(3 MB 초과는 링크).
- **개인정보.** 텔레메트리에 bash 명령 앞 300자가 로컬로 남습니다. 리포트는 실데이터 미리보기를 담습니다 — 공유 전에 확인하세요.
  프로젝트 `.gitignore`는 `data/`·`workspace/`와 Visualization의 플롯 데이터를 기본 제외합니다.
- **현재 공유 범위.** `workspace/`는 프로젝트의 차트·데이터 파일을 상대 경로로 참조하므로 그 폴더만으로 독립 공유를 보장하지 않습니다.
  독립 `build`·`publish`는 위 공유 설계의 후속 구현이며 현재 명령이 아닙니다.

## 개발

```sh
cd $PACK
uv sync
uv run pytest -q                                  # kit, 런처, 배포본(wheel을 빌드해 새 환경에 설치)
uv run --isolated --no-project --python 3.12 --with aelix==0.1.0b2 --with pytest==9.1.1 --with pytest-timeout==2.4.0 \
  python -m pytest -q -p no:cacheprovider --timeout 300 --timeout-method=thread tests/test_extension.py tests/test_usage_report.py \
  tests/test_agent_cli.py                         # aelix 통합 (CI와 같은 환경)
AELIX_ANALYTICS_TEST_INSTALL=1 uv run pytest -q tests/test_packaging.py   # aelix 0.1.0b2와 실제 uv로 끝까지 (네트워크)
uv run ruff check tests extension src tools
```

```text
src/aelix_analytics/          kit이자 배포본의 package. src/가 자식에게 주는 실행 루트(설치본은 aelix_analytics/kit/의 사본)
  __init__.py                 스크립트 API (모듈 수준에서는 stdlib만; kit 모듈은 함수를 부를 때 import)
  __main__.py                 kit CLI (끝 줄이 JSON; 설치된 package 자신으로는 실행 거절)
  core.py                     프로젝트 모델 — stdlib만 (확장과 kit이 공유)
  writer.py                   표식 판정·쓰기 게이트·작성 출처·새 형식의 공통 writer — stdlib만 (core와 함께 로드)
  migration.py                lab.json 프로젝트의 복사 전환 (`analytics migrate`) — stdlib만, aelix 프로세스에는 로드하지 않음
  _platform.py                플랫폼별 잠금·교체·프로세스·run 중단 — stdlib만 (core와 함께 로드)
  relations.py                기록의 관계·해석 상태·상태 재생 읽기 모델(ADR-0006) — stdlib만 (core가 status·리포트에 씀)
  runner.py  capture.py       run 실행과 기록, 입력·차트 캡처 훅
  data.py    workspace.py     import·프로파일·쿼리, Workspace HTML (+ assets/)
  demo.py                     데모 데이터·run (+ demo_scripts/)    template/  새 프로젝트 파일
  cli.py  _install.py         `analytics` 런처, 설치된 실행 루트 찾기    aelix-plugin.toml  manifest
extension/aelix_analytics.py  aelix 확장 (stdlib + aelix API, 실행 루트의 core·_platform·relations·writer를 경로로 로드; 설치본의 aelix_analytics/extension.py)
bin/analytics                 사람용 CLI (체크아웃, POSIX)
```

2026-09-16 확인: kit 39 passed, extension 10 passed(실제 aelix 로더 등록, uv 환경의 import·query·run·재실행·취소 포함).
독립 리뷰에서 나온 16건(차트 정체성, 재실행 판정, stale 계산, 기록 실패 내성, 읽기 전용 쿼리 봉쇄, 계보 오탐,
프로젝트 신뢰 등)을 고치고 항목마다 회귀 시험을 추가했습니다. 설치된 aelix 0.1.0b2에서 RPC 로드와
로컬 모델(ollama) `--print` 세션(lab_status → lab_query → write → lab_run → lab_note)을 실행해 확인했고,
`bin/lab demo` 종단 실행과 리포트 브라우저 확인을 마쳤습니다.

실사용 세션(aelix 세션 텔레메트리 분석, 26 저널 이벤트·6 run) 리뷰에서 나온 7건을 이어서 고쳤습니다:
입력 503개짜리 run이 결과 텍스트·저널 이벤트·리포트를 덮어쓰던 문제(목록·refs·경고에 상한),
`lab_note(refs=["차트-id"])`가 링크되지 않던 문제(짧은 차트 id → `script.py::id` 해석),
차트 이미지 3장 상한 무고지, `lab_import`의 UUID→Binary·중첩 JSON(경고와 텍스트 변환),
이미 import한 외부 입력을 계속 읽을 때의 안내, 생성 run이 없는 데이터셋 표시, CONTEXT.md 주입 한도 경고.

## 관리와 보안

관리자(Maintainer): [@handochan](https://github.com/handochan).
보안 취약점은 공개 Issue에 쓰지 마세요. 알리는 방법과 범위는 [보안 정책](SECURITY.md)에 있습니다.

## 라이선스

[Apache License 2.0](LICENSE).

## Checkpoint와 인계

`analytics checkpoint`는 현재 기준의 결정적 ID/당시 상태 요약을 저장합니다. `analytics checkpoint "사람이 작성한 요약" --ref e7`은
원문과 선택 근거를 남깁니다. `analytics status --part context`에서 최신 checkpoint의 basis·needs_review·updates_available를 확인하고,
`analytics status --part records --id eN`으로 그 원문을 읽습니다. `analytics status --part context --id eN`은 이후 변경 페이지입니다.
SDK는 `analytics.checkpoint()` 또는 `analytics.checkpoint("요약", refs=["e7"])`입니다. 역사 작성에는 as_of_seq와 정확한
context_version 또는 명시적 None을 함께 줍니다. 결정적 생성/기본 읽기는 모델을 부르지 않으며, 실제 결론 검토와 사람/모델 인계 검증은 별도입니다.
[현재 계약 C10](docs/CONTRACTS.md#c10-분석-checkpoint와-결정적-인계-kit-0140-16)을 따릅니다.
