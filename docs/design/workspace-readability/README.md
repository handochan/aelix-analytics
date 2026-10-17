# #95 시안 그림

[Workspace 읽기·추적 화면 개선 설계](../WORKSPACE-READABILITY.md)가 인용하는 시안의 개선안·참조 보드 15장이다.
시안 원본은 소유자 전용 비공개 Design canvas다.

- **시점**: 시안은 2026-10-14 것이다(5·7행 보완 포함). 2026-10-17에 보드를 원래 크기로 headless Chrome에서 그려 손실 없는 WebP로 저장했다.
- **기준은 설계 문서의 글이다.** 그림과 글이 다르면 글을 따른다. 시안 뒤에 정해져 그림과 다른 것은 아래와 같다.
  - 기본 언어: 영어이고 프로젝트 설정은 두지 않는다(설계 §3.4). 8-A 메뉴의 '기본값: 프로젝트 설정(한국어)'은 따르지 않는다.
  - 언어 메뉴: 이름표와 자리는 설계 §3.3과 [계약 C12](../../CONTRACTS.md#c12-workspace-화면-언어-kit-0160-96)를 따른다.
  - 색: run 상자의 남보라는 따르지 않고, 시각화에는 새 토큰 `--k-viz`를 쓴다(설계 §4).
- **예시 값**: #18 QA용 합성 fixture S/M(`47b8739`에서 생성)의 실제 기록이다.
- **넣지 않은 보드**: 비교용 '현재' 보드(1-A, 2-A, 3-A, 5-A, 6-A, 7-A)와 용어표 초안(8-B)이다. 화면 용어는 [영어](../../../src/aelix_analytics/assets/i18n/en.json)·[한국어](../../../src/aelix_analytics/assets/i18n/ko.json) 카탈로그가 기준이다.

| 행 | 보드 | 설계 | Issue |
|---|---|---|---|
| 1-B | [Insights 목록](1-B-insights-list.webp) | §8.1, §8.2 | #97 |
| 2-B | [관계 페이지](2-B-relation-page.webp) | §8.1 | #97 |
| 3-B | [관계 페이지, 390px](3-B-relation-mobile.webp) | §8.3 | #97 |
| 4 | [상태 표시 용어](4-status-labels.webp) | §4, §8.1 | #97 |
| 5-B | [Overview](5-B-overview.webp) | §7.1 | #98 |
| 5-C | [분석 지도 탭](5-C-analysis-map.webp) | §7.2 | #98 |
| 6-B | [시각화 페이지의 버전 기록](6-B-visualization-versions.webp) | §5 | #99 |
| 6-C | [목록의 접힌 카드](6-C-versions-card.webp) | §5 | #99 |
| 7-B | [출처 추적](7-B-provenance.webp) | §6 | #100 |
| 7-C | [출처 추적, 390px](7-C-provenance-mobile.webp) | §6.3 | #100 |
| 8-A | [한국어 화면과 언어 선택](8-A-korean-ui.webp) | §3 | #96(병합됨) |
| 9-A | [데이터 파일 버전](9-A-data-versions.webp) | §5.2 | #99 |
| 9-B | [스크립트 코드 버전](9-B-code-versions.webp) | §5.2 | #99 |
| 9-C | [CONTEXT.md 버전](9-C-context-versions.webp) | §5.2 | #99 |
| 9-D | [버전 표현 공통 규칙](9-D-version-rules.webp) | §5.1 | #99 |

## 1–4행 목록·관계·용어

![1-B Insights 목록: 한 줄 행, 아이콘과 글자의 근거 배지, 종류별 목록 필터](1-B-insights-list.webp)

![2-B 관계 페이지: 기록 글이 제목이고, 정정 안내와 '가리키는 것·가리키는 기록'으로 묶은 관계, 작은 지도](2-B-relation-page.webp)

![3-B 390px 관계 페이지: 한 줄 상단 막대, 44px 이상 버튼, 아래에 접힌 지도와 기술 세부](3-B-relation-mobile.webp)

![4 상태 표시 용어: 저장 값과 지금 표시, 제안 표시, 쉬운 뜻](4-status-labels.webp)

## 5행 Overview와 분석 지도

![5-B Overview: 현재 분석 방향, 따로 보이는 분석 상태 6칸, 분석 지도 요약](5-B-overview.webp)

![5-C 분석 지도 탭: 스크립트와 run 육각형의 주요 흐름 3개, 살펴볼 것, 읽는 법](5-C-analysis-map.webp)

## 6행 시각화 버전

![6-B 시각화 페이지: 버전 번호와 시각, 시간축 띠, 같은 코드·입력 묶음, 버전별 인용 수](6-B-visualization-versions.webp)

![6-C 목록의 접힌 카드: 최신 버전 번호와 시각, 최신 버전 안내, 접힌 버전 띠](6-C-versions-card.webp)

## 7행 출처 추적

![7-B 출처 추적: 가져온 데이터에서 run, 파생 데이터, 시각화, 인사이트까지의 그래프와 확인할 수 있는 것, 글 목록](7-B-provenance.webp)

![7-C 390px 출처 추적: 세로 경로](7-C-provenance-mobile.webp)

## 8행 한국어 화면

![8-A 한국어 Overview와 언어 메뉴. 메뉴의 기본값 문구는 나중 결정(설계 §3.4)과 다르다](8-A-korean-ui.webp)

## 9행 다른 버전 기록

![9-A 데이터 파일 버전: t10_delivery_delay의 14개 버전과 읽은 run](9-A-data-versions.webp)

![9-B 스크립트 코드 버전: v2 실패 뒤 v1로 돌아갔고, v3에서 입력이 바뀜](9-B-code-versions.webp)

![9-C CONTEXT.md 버전: 13개 버전, 고정 섹션은 v2 이후 그대로](9-C-context-versions.webp)

![9-D 버전 표현 공통 규칙](9-D-version-rules.webp)
