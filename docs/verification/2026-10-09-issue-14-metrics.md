# Issue #14 — 지표 정의·분석 초점·명시적 관측의 측정 기록

작성: 2026-10-09 · 상태: 리뷰·수정 diff 확인 완료, CI 대기 · [Issue #14](https://github.com/handochan/aelix-analytics/issues/14)
기계 기록은 [같은 이름의 JSON](2026-10-09-issue-14-metrics.json)이다.

## 기준과 변화

측정 코드 `7c59ae9e439135a78c071ed4a46738fb2ba7724a`의 소스·시험·실행 도구·레코드 schema 222개 파일 manifest SHA-256은 `7fd727fc358dc41ecb660794631ba7977b1036f4b4812f22f9a64872c9ffef2d`이다.
로컬 manifest·JUnit·명령 출력은 Issue별 run의 `.devstate`에 보존한다. 사용자 데이터나 실제 프로젝트 절대 경로를 fixture로 옮기지 않았다.

kit 0.9.0은 기존 `analytics.event/2`에 세 상세 계약을 추가한다: `metric_definition/1`, `analysis_focus/1`, `metric_observation/1`.
별도의 모델 도구를 추가하지 않았고 CLI·SDK·기존 analytics_record가 공통 writer를 부른다. 원래 이벤트·정의·부모 버전·관측을 보존한다.
기본 read projection은 실행·기록·미검증 계산을 구별하며 목적 초점만 연결된 run을 계산 관측으로 간주하지 않는다.

## 리뷰와 지적 처분

설계 기준 `a9b54b2`에 독립 리뷰와 별도 Codex 리뷰를 각각 1회 받았다. 독립 D14-I1은 구조화 수정에 caller가 추가한 unrelated supersedes/updates/resolves로
다른 현재 기록을 숨길 수 있다는 P2 지적이었다. details를 상태 변경의 정본으로 두고 caller 상태 변경 참조를 거절하도록 ADR을 수정했다.
`5607c79` 수정 diff는 한 번 확인했다. 구현 기준 `dae6b5a`에 독립·Codex 리뷰를 각각 1회 받았다. I14-1/C14-1은 결과 참조의 잘못된 digest 또는 아직 쓰이지 않은 결과를 즉시 append할 수 있는 P2였다.
`f462787`에서 실제 append의 결과 membership·선택적 digest를 기록된 버전과 비교하고, 향후 결과 허용은 SDK의 명시적 pending precheck에만 두었다.
C14-2는 자유 본문에 JSON 옵션 이름이 들어가면 해석을 바꿔 기록하지 않는 main 회귀였다. 명시적으로 인용한 text 뒤의 옵션만 해석하고 기존 문장·따옴표·apostrophe를 보존했다.
수정 diff는 한 번 확인했다. 그때 드러난 도구 설명의 "모든 kind가 resolves 가능" 문장도 일반 kind로 제한해 바로잡았다. 추가 전체 리뷰는 하지 않았다.

#83 병합 `44ae234` 위로 rebase했다. 이전 수정 tree와 다른 것은 upstream의 model_eval.py·그 시험·안내 세 파일이며 #14 runtime은 같음을 확인했다.

## 검증

| 검증 | 기준 | 결과와 범위 |
|---|---|---|
| 기존 writer/record/read-view/layer 기준선 | 설계 `a9b54b2` | 283 passed, 10.76s |
| 새 M0·최소 M1 및 관련 record/gate/layer 시험 | 초기 구현, 1108110 전 | 385 passed, 29.61s. 이후 역사 metadata 처리 보강은 다시 검사 중 |
| 최종 focused 시험 | `7c59ae9e439135a78c071ed4a46738fb2ba7724a` | 711 passed·10 skipped, 73.28s. standalone reader·기존 형식·line endings·packaging까지 포함. skip 사유는 JSON에 기록 |
| lint·문서 | 구현 | ruff pass; 68 Markdown·14 JSON·736 local links, errors 0 |
| macOS 로컬 저장소 전체 | 1108110에서 시작 | 중단: 1508 passed·6 failed·76 skipped, 521.22s. 버전/kind 문서·시험 4곳과 SDK resolve 호환성은 수정했고, 실행 중 코드 변경의 kit hash 불일치는 혼합 소스 한계로 보존. parent HOME 등은 task 폴더로 격리. 최종 exact-head CI 필요 |
| aelix 3.12 모델 없는 통합 | 1108110에서 시작 | 964 passed, 56.92s. metadata 보강이 실행 중 반영되어 최종 exact-head CI를 별도로 확인 |
| 리뷰 수정의 관련 시험 | f462787 | 502 passed, 24.74s. 결과 버전·pending 최종 재검사 포함 |
| 리뷰 수정의 aelix 3.12 통합 | f462787 | 971 passed, 54.14s. 일반 문장의 옵션 언급·인용·apostrophe·구조화 명령 포함 |
| Linux·Windows CI | 최종 PR head | not_run |
| macOS CI | 최종 PR head | not_run |

잘못된/미지원 tag 기록은 상태 재생에서 target을 바꾸지 않고 invalid로 보존한다. standalone stdlib reader를 유지하도록 공유 순수 규칙은 relations.py에 둔다.
검증 사례는 미정 후보·다중 역할, 정의 수정·부모 버전 고정, 조건별 관측·코드만 수정한 재계산, 목적/계산 구별, run 시작 초점 보존,
동시 수정 거절, 거절의 무부수효과, 실패/취소 run의 명시적 관측, 부분 최종 쓰기 실패, SDK payload 사본, CLI/SDK 공통 계약,
원래 비구조화 기록 읽기, 보존된 결과 표 참조다. 실제 모델 호출이나 자동 식 검증으로 넓혀 주장하지 않는다.

## 남은 범위와 한계

실제 모델 E2E·tokenizer/토큰 비용, 사용자 프로젝트 전환, 외부 파일 시스템, 릴리스/게시, #15 검색·#16 checkpoint·#18 상세 UI는 not_run이다.
참조는 run 전체와 선언한 결과를 연결하며 함수 수준 실행 계보를 증명하지 않는다. `calculation_check`는 기본 unverified다.
run 안의 pending 응답은 사본을 대기열에 넣었다는 뜻이다. 나중에 입력 참조가 늘어 줄 상한을 넘거나 쓰기가 실패하면 finalize_errors에 남고 관측 성공으로 표시하지 않는다.
기존 tag 없는/잘못된 기록에 현재 정의를 소급해서 채우지 않는다. CI·구현 리뷰 결과가 나오기 전에는 #14 완료로 판정하지 않는다.
