# 보안 정책

Aelix Analytics는 첫 베타를 준비하고 있고 아직 어떤 버전도 게시하지 않았습니다. 패키지 색인의 패키지·버전 태그·GitHub 릴리스가
없고, wheel은 체크아웃에서 빌드합니다([변경 이력](CHANGELOG.md)). 어떤 Python·운영체제·aelix·설치 경로에서 설치하고 실행했는지는
[지원 범위](docs/SUPPORT.md)에 있습니다.

## 취약점 알리기

**취약점을 공개 Issue·pull request·discussion에 쓰지 말고, 그 내용을 commit에 넣지 마세요.**

취약점은 GitHub의 비공개 취약점 신고로 받습니다. 저장소의 **Security** 탭에서 **Report a vulnerability**를 고릅니다. 이렇게 보낸
신고는 공개되지 않은 채 관리자에게 갑니다.

저장소는 지금 비공개이고, 비공개 취약점 신고는 저장소를 공개할 때 켭니다. 그 전에는 비공개로 신고할 경로가 없습니다. 그동안에도
취약점의 내용을 Issue·pull request·commit에 쓰지 마세요. 비공개 저장소에서도 모든 collaborator가 읽을 수 있습니다.

## 신고에 적을 것

- 그 취약점으로 무엇을 할 수 있고, 그러려면 무엇이 필요한지. 예: 프로젝트, 데이터 파일, 사람이 여는 스크립트나 메모, aelix 세션,
  공유한 Workspace 폴더.
- 영향을 받는 부분(아래 '범위')과 그 버전: 체크아웃이면 commit, 설치본이면 wheel의 버전. run은 그 run을 실행한 kit의 버전을
  `runs/<id>/run.json`의 `env.kit_version`에 남깁니다.
- 설치 방법: `uv tool install`로 설치한 wheel, aelix의 환경에 설치한 wheel, 체크아웃(`bin/analytics`, 또는 aelix에 준 확장 파일).
  운영체제, Python 버전, aelix 버전.
- 합성 데이터로 재현하는 단계. 실제 데이터·자격 증명·세션 로그는 보내지 마세요.
- 기대한 것과 실제로 일어난 것. 고칠 방법이 있으면 그것도.

## 아직 정하지 않은 것

정한 것은 위의 신고 경로와 공개 Issue로 받지 않는다는 것입니다(2026-10-07 소유자 결정,
[#24](https://github.com/handochan/aelix-analytics/issues/24)). 다음은 아직 정하지 않았고, 정하면 이 문서에 적습니다.

- 보안 수정을 받는 버전과, 수정을 이전 버전에 반영하는지.
- 신고를 받은 뒤의 처리 절차와 응답 시간.
- 수정 뒤의 공지(GitHub security advisory를 게시하는지와 그 시점).

## 범위

범위 안은 이 저장소의 코드입니다.

- kit: `analytics` 명령줄의 명령, SDK(`import aelix_analytics as analytics`), run 기록, kit이 그리는 Workspace HTML
  (`src/aelix_analytics/`).
- aelix 확장: 그 도구, `/analytics` 명령과 hook(`extension/aelix_analytics.py`, 설치본에서는 `aelix_analytics.extension`).
- 런처: 설치본의 `analytics`와 체크아웃의 `bin/analytics`.
- `tools/` 아래의 평가·측정 도구(모델 평가 하네스, 사용량 보고, 평가 fixture 생성기 등).

범위 밖은 다음입니다.

- aelix 자체: host, 확장 로더, aelix의 도구와 모델 provider. aelix 프로젝트에 알려 주세요.
- 사람이나 에이전트가 프로젝트에서 쓰고 실행하는 분석 코드와, 그 코드가 읽는 데이터.
- 분석 프로젝트가 설치하는 라이브러리(DuckDB, Polars, matplotlib, Plotly 등)와 uv. 각 프로젝트에 알려 주세요. kit이 그것을
  안전하지 않게 쓰는 방식은 범위 안입니다.

문서에 적힌 동작은 그것만으로 취약점이 아닙니다. Aelix Analytics는 샌드박스가 아닙니다. `analytics_run`은 받은 코드를 사용자의
권한으로 실행하고, aelix는 확장 도구를 실행하기 전에 묻지 않으며, Workspace는 데이터 미리보기와 로그를 가리지 않고 보입니다
([README](README.md#한계), [현재 계약 G](docs/CONTRACTS.md#g-약속하지-않는-것)). 문서가 약속하는 경계를 넘는 방법은 범위 안입니다.
예를 들면 다음과 같습니다.

- `analytics_query`로 보낸 쿼리가 읽기 전용이 아니거나 프로젝트의 `data/` 밖 파일에 닿는 것, 작업 디렉터리만으로 찾은 프로젝트가
  신뢰 목록에 없는데도 활성화되는 것([현재 계약 E](docs/CONTRACTS.md#e-계산-규칙)).
- 사람이나 스크립트가 기록한 글(메모, 제목, 열 이름, 경로)을 Workspace가 글자로 보이지 않고 HTML이나 스크립트로 실행하는 것
  ([현재 계약 F1](docs/CONTRACTS.md#f1-이전-구현48b79cc과-달라진-posix-동작)의 #10 '사용자 내용(Workspace)',
  [용어와 언어 §7](docs/TERMINOLOGY-AND-LANGUAGE.md#현재-구현10)).
