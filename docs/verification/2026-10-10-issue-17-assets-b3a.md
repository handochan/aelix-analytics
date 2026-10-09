# #17 B3-a — 분석 자산 후보·검사·등록 측정

작성: 2026-10-10 · 상태: **구현 리뷰 전, 임시 #14 stack 검증**. #17 전체 완료가 아니다.
정본 집계는 [JSON](2026-10-10-issue-17-assets-b3a.json)이다. 실제 구현·미실행 범위는 [계약 C7](../CONTRACTS.md#c7-분석-자산의-후보검사명시적-등록-kit-0100-17-b3-a)을 따른다.

## 코드 기준

측정 commit: `1a786bacc4cd2406eaf142233824bfc69791dae7`. 공유 #14 기반: `a881e0d795c21b8292ffb1897e34a5fcfe800686`(아직 main 병합 전인 명시적 의존).
#14 변경을 #17 산출물로 셈하지 않는다. Source 코드 manifest sha256: `d4cb5d299047460e210c88a00676972b2365686f15e0473c6cf3062d4ffdf444`.
metric floor 0.9.0, asset floor/kit 0.10.0. 최종 main rebase 뒤 코드 기준·검증·PR CI를 다시 확정해야 한다.

## 확인한 범위

| 검증 | 환경 | 결과 |
|---|---|---|
| 자산/순수 계약/standalone reader/writer/marker/layers pytest | macOS, Python3.12.13, parent+child HOME/trust/agent scope | **495 passed** |
| 확장/사용량/agent CLI pytest | macOS, isolated aelix0.1.0b2, Python3.12, scoped parent/child | **972 passed** |
| SKILL.md-only 등록과 actual load_skills discovery | explicit synthetic project skill dir만 | 위 host suite에 포함, pass |
| ruff tests/src/extension/tools | 위 commit | pass |
| docs links/examples | 위 commit | 70 Markdown,14JSON,745 local links, pass |

실제 stdlib run의 파일 버전·출력 assert로 원래 입력/별도 입력/경계 검사를 만들었다.
module과 SKILL.md-only 후보를 각각 trial/validated로 승격하고 명시적 project destination에 바이트 그대로 등록했다.
기존 동일 바이트 목적지의 무소유 채택을 거절하고, journal 실패 뒤 published receipt가 남은 등록만 이어서 완료한다.
canonical journal 성공 뒤 private receipt 갱신 실패는 registered=true와 경고이며 미등록으로 뒤집지 않는다.
malformed envelope/상태 참조/빈 등록 receipt와 mismatched human update는 상태·점수 근거에 쓰지 않는다.
SDK의 실행 중 검사 선언은 실제 run 완료 관측으로 다시 검증한다. 초기 후보·자유 분석은 사전 등록을 요구하지 않는다.

이전 broad 실행의 916pass/1skip/2fail은 현재 통과로 덮지 않는다. 두 실패는 kit version/public kind의 오래된 고정 assertion이었고
해당 assertion을 갱신한 affected 실행240pass와 이후 현재-target495pass로 확인했다. 원시 로그·명령·HOME scope·snapshot은 로컬 run의 evidence에 보존한다.

## 리뷰와 미실행

설계는 독립/Codex 리뷰와 한 번의 correction diff 확인을 마치고 PR85로 병합했다.
이 구현의 독립/Codex 리뷰와 correction diff 확인은 **pending**이다. 런타임 PR1 CI도 **not_run**이다.

B3-b의 모드/롤링 창/자동 발행과 B3-c 세션/맥락/UI/Q11은 아직 구현하지 않았다.
Linux/Windows current-target CI, Python3.11 host, 설치 wheel/sdist, 실제 모델·사람 리허설, 실제 사용자 프로젝트 등록은 미실행이다.
loader port가 없는 stdlib CLI/SDK의 loader_discovery=not_run을 pass로 쓰지 않는다. 등록·발견·실행·유용성은 별개다.
전역 등록·릴리스·외부 게시는 하지 않았다.
