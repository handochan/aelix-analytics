# ADR-0010 — 분석 자산의 이름과 후보·검사·제안 기록

상태: Accepted · 작성: 2026-10-09 · 채택: [PR85](https://github.com/handochan/aelix-analytics/pull/85) · 작업: [#17](https://github.com/handochan/aelix-analytics/issues/17)

## 배경

사용자는 #17 착수 때 “재사용”보다 적절한 용어로 구현하기를 요청했다. 이 기능은 코드를 다시 실행하는 횟수만 세는 기능이 아니다.
계산 구현과 사용 절차를 후보로 남기고, 적용 조건을 검사한 뒤 선택한 프로젝트에 등록하는 경로다.
[ADR-0008](0008-reuse-proposal-counting-and-deterministic-always-on.md)의 제안 정책은 이미 채택했지만 공개 진입점과 구체 필드는 아직 구현하지 않았다.
현재 reader는 `reuse_proposal`·`reuse_notification`·`reuse_mode`를 읽으며, writer는 이 종류를 공개 기록으로 받지 않는다.

## 결정

1. 제품 용어는 **분석 자산 / Analysis asset**, 작성 중인 대상은 **자산 후보 / Asset candidate**, 알림 행동은
   **자산 제안 / Asset suggestion**이다. 자산의 산출물은 `function`, `module`, `package`, `skill`이다.
   Skill은 절차, 나머지는 계산 구현이며 둘 다 만들어야 하는 것은 아니다. 등록·발견·실행·유용성은 별개다.
2. 새 공개 기능은 `assets` 이름을 쓴다. `analytics.event/2`에 `asset_candidate`, `asset_check`, `asset_effect`,
   `asset_notification`, `asset_mode`를 추가한다. 후보의 논리 키는 `asset_id`, 정확한 버전 참조는
   `{"type":"asset_candidate","asset_id":"growth-rate","event_id":"e12",...}`다.
   구체 필드·쓰기 검증·승격은 [구현 계약](../design/ANALYSIS-ASSETS.md)에 있다.
3. 기존 `reuse_*` 종류·`proposal_id`·`reuse_proposal` 참조를 reader와 schema에서 제거하거나 다시 쓰지 않는다.
   과거 기록은 원래 정체성으로 읽고 Asset candidate/suggestion labels로 표시하되 legacy 종류를 상세에서 확인할 수 있다.
   새 writer는 옛 이름을 새 입력 별칭으로 받지 않는다. 과거 후보를 새 ID로 자동 변환하지 않는다.
   새 기능에서 과거 후보를 발전시킬 때는 명시적 `derived_from`으로 연결한 새 후보를 쓰며, 원래 이력도 제안 억제에 합친다.
4. 새 프로젝트 설정 키는 `asset_suggestions`다. ADR-0008의 미구현 `reuse_proposals` 예시를 대체하되
   `on_demand`·`always_on`, 초기 점수 8, 롤링 24시간, 요청 구분·거절·확정 근거·모델 없는 판정의 의미는 바꾸지 않는다.
   옛 설정 키가 있으면 상태에 `legacy_asset_settings` 오류와 새 키를 안내하고 자동 제안을 중단한다. 조용히 옮기거나 활성화하지 않는다.
5. 새 기록의 쓰기 검증은 공통 writer가 수행한다. `analytics_record`·SDK·CLI·hook가 이를 우회하지 않는다.
   첫 구현 PR은 kit 버전을 올리고 새 기록을 쓰기 전에 `min_writer_version`을 그 버전으로 올린다(ADR-0006 1.3).
   #14와 같은 버전을 서로 다른 쓰기 규칙으로 출하하지 않는다. 병합 순서에 따라 다음 공개 버전을 쓰며 기능의 최초 버전은 상수로 보존한다.
6. 구현은 검증 가능한 세 PR로 나눈다. **B3-a** 후보·검사·효과·상태·명시적 등록·요청 시 경로,
   **B3-b** 공통 결정적 ProposalPolicy·프로젝트 모드·CLI 발행·동시성,
   **B3-c** Aelix 세션 모드·맥락 블록 발행·Workspace 조회·Q11 계측이다.
   B3-a/B3-b의 중간 버전은 미구현 상시/호스트 기능을 활성화하거나 완료로 안내하지 않는다.
   #17은 세 PR과 필요한 CI·리뷰·측정이 끝나기 전 닫지 않는다.

## 결과와 검증

기존 프로젝트의 재전환은 없다. 새 kind/type/선택 필드만 더한다. 과거 기록의 의미·바이트·원래 참조가 유지된다.
목록이나 상세 읽기는 모델 호출·등록·실행을 하지 않는다. 후보 작성은 자유 분석의 선행 조건이 아니다.
새 분석법의 자동 추출, 공용 skill 배포, 함수 호출 단위 계보, 모델 기반 자동 검토는 범위 밖이다.

채택 PR85는 설계다. 현재 구현과 검증 범위는 [현재 계약](../CONTRACTS.md)의 C7·C8과 버전별 측정 기록을 따른다.
구현 전 독립 리뷰·Codex 리뷰, 구현 PR의 두 리뷰·수정 diff 확인과 md+JSON 측정은 운영 정책을 따른다.
