# ADR-0012: checkpoint의 기준과 결정적 인계

상태: Proposed · 2026-10-10 · 작업: [#16](https://github.com/handochan/aelix-analytics/issues/16)

## 맥락

[ADR-0006](0006-common-record-contract.md) 1·2·5·6절은 `analytics.event/2`의 `checkpoint` 종류,
불변 이벤트 ID, `details.as_of_seq`·`context_version`, 정정·철회의 읽기 계약을 이미 채택했다.
[연속성 설계](../DESIGN-CONTINUITY-AND-REUSE.md#작업-구간-요약-checkpoint)는 작업 구간 요약과
`needs_review`·`updates_available`의 구별, 숨은 모델 호출 없는 최초 요약을 정했다.
kit 0.11.0은 이 종류를 읽지만 checkpoint 작성·평가·인계 기능을 구현하지 않았다.

이 결정은 그 범위의 writer·reader·공개 진입점을 구체화한다. 원문·기존 종류·작성 주체의 뜻을 바꾸거나
새 가족으로 전환하지 않는다. 자세한 필드·충돌·시험은 [구현 전 설계](../design/CHECKPOINT-HANDOFF.md)에 있다.

## 제안하는 결정

1. 새 writer는 기존 `analytics.event/2`, `kind=checkpoint`에 `details.contract=analysis_checkpoint/1`을 쓴다.
   `as_of_seq`는 null이 아닌 정수이고 checkpoint 자체보다 앞의 journal 기준이다. CONTEXT는 정확한 버전과
   digest를 참조하거나 `context_version=null`로 미확인을 남긴다. as_of만으로 CONTEXT를 추정하지 않는다.
2. 요약의 작성 방식(`authored`·`deterministic`)과 기존 `Origin`의 작성 주체·실행 수단을 구별한다.
   결정적 요약을 요청한 사람·에이전트를 `system`으로 바꾸지 않는다. session은 선택적인 출처이며 신원 인증이 아니다.
3. 저장한 요약·원문 참조·기준은 불변이다. 새 checkpoint·정정·철회가 이전 본문과 근거를 덮어쓰지 않는다.
   checkpoint 자체에 새 상태 전이를 만들지 않는다. 후속 평가 값은 reader가 계산하며 원장에 다시 쓰지 않는다.
4. `needs_review`는 요약이 선택한 판단·근거의 변화 또는 확인 불가를, `updates_available`는 기준 뒤의 새 기록을
   각각 표시한다. 둘은 동시에 성립할 수 있다. checkpoint 자체의 append는 새 변경 묶음에서 제외한다.
   참조가 없는 자유 본문의 진실·빠진 의존성을 자동 판정하지 않는다.
5. 기본 인계는 현재 checkpoint 평가와 제한된 후속 기록을 읽는다. 현재 근거의 물리 가용성은 역사적 journal
   판단과 별도로 표시한다. 근거의 `resolved`나 `needs_review=false`를 결론 검토·분석 검사 통과로 올리지 않는다.
6. 결정적 요약·읽기에는 모델 호출을 넣지 않는다. CLI·SDK의 명시적 checkpoint 작성과 기존 record 도구의
   authored checkpoint를 같은 응용·writer로 처리하며, 새 상시 모델 도구와 매 턴 자동 저장은 추가하지 않는다.
7. 태그 없는 기존 `/2` checkpoint는 계속 일반 기록으로 읽고 기본 기준 필드를 보인다. 새 태그의 구조화 요약을
   소급해서 채우거나 그 기록을 `unknown_kind`로 바꾸지 않는다. 더 새로운 태그의 전문 평가만 미지원으로 표시한다.
8. 실제 도입 버전과 checkpoint 기능 floor는 0.14.0으로 예약한다. 최초 쓰기 전 같은 writer 잠금 안에서
   `min_writer_version`을 올린다. metric 0.9.0·asset 기본 0.10.0·후속 asset mode 0.12.0의 floor는 유지한다.
   이 설계 PR은 runtime이나 버전을 바꾸지 않으며, 실제 B3-b 0.12.0·B3-c 0.13.0 main 이후 구현·최종 검증한다.

## 비교와 한계

별도 요약 파일을 정본으로 두면 journal 기준·참조·작성 출처를 중복 관리해야 한다. CONTEXT를 자동으로 덮어쓰면
사람의 현재 작업 방향과 과거 요약이 섞인다. 기존 checkpoint 이벤트와 재생성 가능한 reader 평가를 선택한다.

기본 status를 전체 checkpoint 덤프로 바꾸거나 요약용 모델 도구를 더하지 않는다. 일반 record는 명시적으로 작성한
본문을 저장하고, CLI·SDK의 별도 편의 함수가 결정적 생성을 제공한다. 토큰·의미 회수·실제 인계의 성공은 설계로
선언하지 않으며 Q2·Q12·Q17의 합성 검증과 실제 모델·사람 검증을 별도로 기록한다.
