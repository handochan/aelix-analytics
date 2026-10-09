# #17 B3-a — 분석 자산 후보·검사·등록 측정

작성: 2026-10-10 · 상태: **구현 리뷰·수정 검증 완료, 의존 branch의 CI 차단**. #17 전체 완료가 아니다.
정본 집계와 source manifest는 [JSON](2026-10-10-issue-17-assets-b3a.json)이다. 구현·미실행 범위는 [계약 C7](../CONTRACTS.md#c7-분석-자산의-후보검사명시적-등록-kit-0100-17-b3-a)을 따른다.

## 코드 기준

측정/재구성 commit: `58d5b406e97a4e16706d6a3ac7f7928a7d436b2b`. 공유 #14 최종 branch 기반: `166f991c50f37ea7de583ce2014a43afca342246`(아직 main 병합 전).
수정 검증 commit `e9c2d793831ba8d1b5a55e3f04554d5a51795d09`와 **226개 코드 파일의 내용이 같다**.
manifest sha256: `eded8dcb74491549475a8157a0d58acb63d4b51331979a4d4b3a8ff6be9f8a6d`. 추가 차이는 README wheel 이름·CONTRACTS 배포 버전의 문서 정정뿐이다.
#14 변경과 이미 병합한 PR85 네 설계 문서를 #17 runtime diff로 셈하지 않는다. metric floor 0.9.0, asset floor/kit 0.10.0.
원래 stack은 별도 backup branch로 보존했다. 실제 main rebase·최종 CI·병합 날짜는 차단 해제 뒤 확정해야 한다.

## 확인한 범위

| 검증 | 기준/환경 | 결과 |
|---|---|---|
| 수정 영향: 자산/계약/standalone reader/writer/marker/layers | e9c2d79, macOS Python3.12.13, scoped parent+child HOME | **497 passed** |
| 전체 저장소·wheel/sdist·실제 설치 E2E | e9c2d79, AELIX_ANALYTICS_TEST_INSTALL=1 | **2070 passed,76 skipped,2 version-doc failures** (685.41s) |
| 위 두 문서 버전 실패의 영향 검사 | 58d5b406e97a4e16706d6a3ac7f7928a7d436b2b | **2 passed**. 전체 실행을 소급해 green으로 쓰지 않음 |
| 고정 aelix0.1.0b2 host/usage/agent CLI | Python3.12 1a786ba / Python3.11 e9c2d79, scoped HOME | 각각 **972 passed** |
| SKILL.md-only 등록과 실제 load_skills discovery | explicit synthetic project skill dir만 | 위 pinned host suite에 포함, pass |
| 실제 kit CLI 명시적 등록 | e9c2d79, 성공·별도 입력·경계 검사 뒤 | byte/journal 보존·공통 reader registered, pass |
| Python3.11 stdlib-only platform smoke | e9c2d79, macOS | required hold, 15 holds/3 observed/2 not_run |
| ruff / docs links/examples | e9c2d79 | pass / 71 Markdown,15JSON,747 local links pass |

실제 stdlib run의 파일 버전·출력 assert로 원래 입력/별도 입력/경계 검사를 만들었다.
module과 SKILL.md-only 후보를 각각 trial/validated로 승격하고 명시적 project destination에 바이트 그대로 등록했다.
기존 동일 바이트 목적지의 무소유 채택을 거절하고, journal 실패 뒤 owned published receipt가 남은 등록만 이어서 완료한다.
canonical journal 성공 뒤 private receipt 갱신 실패는 registered=true와 경고이며 미등록으로 뒤집지 않는다.
malformed envelope/상태 참조/빈 등록 receipt와 mismatched human update는 상태·점수 근거에 쓰지 않는다.
SDK의 실행 중 검사 선언은 실제 run 완료 관측으로 다시 검증한다. 초기 후보·자유 분석은 사전 등록을 요구하지 않는다.

이전 broad 실행916pass/1skip/2fail은 보존한다. stale version/public-kind assertion 수정 뒤 affected240pass와495pass를 확인했다.
최신 전체 실행의 두 실패는 README wheel 이름·CONTRACTS 배포 버전이0.9.0으로 남은 문서 오류였다. 이를0.10.0으로 고쳐 해당2검사를 다시 통과시켰다.
원시 로그·정확한 명령·HOME scope·snapshot은 로컬 run evidence, skip 이유는 JSON에 있다.

## 리뷰와 CI·미실행

설계 독립/Codex 리뷰와 한 번의 correction diff 확인 후 PR85를 병합했다.
구현은 고정782e363에 별도 독립/Codex 리뷰를 받았고 같은 두 P2를 재현했다: private replay의 prior chronology 불일치,
후보 artifact/source/check identity와 관계없는 promotion proof. e9c2d79에서 공통 Replay와 journal-only identity 검증으로 고쳤다.
수정 diff782e363..e9c2d79의 **한 번 확인도 통과**했다. 원본 줄은 보존하고 잘못된 전이는 경고/미적용으로 남는다.
현재 파일이 나중에 바뀌어도 과거 journal proof의 정체성 검증을 live bytes로 다시 쓰지 않는다.

Draft [PR88](https://github.com/handochan/aelix-analytics/pull/88)은 #14 branch를 base로 두어 고유 diff만 담는다.
런타임 [CI37962902000 attempt1](https://github.com/handochan/aelix-analytics/actions/runs/37962902000)은 정확한 HEAD58f7ac1의5개 job이
같은 결제/사용 한도 사유로0step이며 시험 **not_run**이다. GitHub conclusion은 failure이고 macOS는 아직 dispatch하지 않았다.
실제 main rebase와 최종 세 OS CI 통과는 **pending**이다. 선행 PR87 마지막166f991의 [CI37960224053 attempt1](https://github.com/handochan/aelix-analytics/actions/runs/37960224053)은
계정 결제/사용 한도 annotation으로5개 job이 **step을 시작하지 못했다**. 이것을 코드 시험 실패나 CI 통과로 바꾸지 않는다.
실제 merged main과 최종 CI 조건을 채우기 전에는 이 runtime을 병합하지 않는다.

B3-b 모드/롤링 창/자동 발행, B3-c 세션/맥락/UI/Q11은 아직 구현하지 않았다.
Linux/Windows current-target CI, 실제 모델·사람 리허설·사용자 프로젝트 등록은 미실행이다.
loader port가 없는 stdlib CLI/SDK의 loader_discovery=not_run을 pass로 쓰지 않는다. 등록·발견·실행·유용성은 별개다.
전역 등록·릴리스·외부 게시는 하지 않았다.
