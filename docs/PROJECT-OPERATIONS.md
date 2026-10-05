# Codex·Claude Code 공통 프로젝트 운영 정책

작성: 2026-09-19 · 상태: 운영 정책 초안. Project 7 연결·초기 Issue 등록은 완료했다. 저장소 CI는 `.github/workflows/ci.yml`([기여 가이드](../CONTRIBUTING.md#저장소-ci))이며, 자동 배정·동기화 자동화는 후속 작업이다.
2026-10-03: 소유자 결정([#39](https://github.com/handochan/aelix-analytics/issues/39))으로 다음을 더했다: [리뷰 예산](#리뷰-예산), 측정 기록의 범위, 승인된 다음 작업의 자율 착수, 두 Issue의 병행.
적용 대상은 `handochan/aelix-analytics` 개발 작업이다. 분석 사용자가 만드는 프로젝트의 `analytics.json`·journal·run 계약과 구별한다.

## 1. 어디에 무엇을 남기는가

| 정보 | 기준이 되는 위치 | 로컬의 역할 |
|---|---|---|
| 작업 목적·범위·수용 조건·의존성 | GitHub Issue | 선택한 작업의 재개 요약 |
| 우선순위·진행 상태·출시 묶음 | 연결된 GitHub Project·Milestone | 마지막으로 확인한 상태와 미동기화 표시 |
| 채택된 기술 결정 | Git에 보존한 Accepted ADR | 현재 작업의 관련 결정 링크 |
| 제안과 설계 | Git의 Proposed ADR·설계 문서와 Decision Issue | 검토 중인 가정·선택지 |
| 실제 동작 | 코드·현재 계약·해당 버전의 검증 결과 | 작업 사본과 실행 증거 |
| 변경·리뷰·병합 | PR·commit·검증 실행 | 재현 명령과 아직 올리지 않은 변경 |
| 중단 지점·다음 행동·실패한 접근 | Issue/PR의 handoff 요약, 보존한 코드 branch | `.devstate/`의 자세한 재개 정보 |

Project에 설계 본문을 복제하지 않고 Issue와 저장소 문서의 링크를 사용한다. Issue에 모든 세션 대화를 붙이지 않는다.
에이전트 메모리와 로컬 상태는 참고 자료다. 오래된 로컬 상태로 GitHub의 최신 수용 조건이나 채택된 ADR을 되돌리지 않는다.
서로 충돌하면 실제 코드 기준·Issue 갱신 시각·ADR 상태를 비교하고, 영향을 받는 결정만 명확히 한다. 사용자에게 승인받은 범위는 도구가 바뀌어도 유지한다.

## 2. 공통 지침의 진입점

- [AGENTS.md](../AGENTS.md)에 짧은 공통 규칙과 문서 링크를 둔다.
- [CLAUDE.md](../CLAUDE.md)는 `@AGENTS.md`로 같은 내용을 불러온다. 별도의 개발 절차를 복사하지 않는다.
- 자세한 절차는 이 문서, 검증 명령은 [CONTRIBUTING.md](../CONTRIBUTING.md), 제품의 출시 기준은 [BETA-QUALITY-GATES.md](BETA-QUALITY-GATES.md)에 둔다.
- 정책·템플릿은 Git으로 공유하고, 도구의 캐시·개인 설정·로컬 로그는 공유 지침에서 분리한다.

Codex의 AGENTS.md 탐색과 Claude Code의 `@AGENTS.md` import는 각 도구의 공식 경로다. 설정 파일이 있다는 사실만으로 모든 규칙이 기술적으로 강제되는 것은 아니다.
[Codex 공식 문서](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [Claude Code 공식 문서](https://code.claude.com/docs/en/memory).

## 3. GitHub Project 연결과 관리

새 Project는 사용자가 직접 만든다. 같은 이름의 기존 Project는 이 저장소의 운영 범위 밖이므로 읽은 내용을 작업 큐로 가져오거나 설정·항목을 수정하지 않는다.
사용자가 알려 준 URL만 [.github/project.json](../.github/project.json)의 `project_url`에 기록한다. `null`은 미연결 상태다.
2026-09-19에 사용자가 지정한 작업 보드는 [Project 7](https://github.com/users/handochan/projects/7/views/1)이다. 같은 이름의 다른 Project와 구별한다.
이 파일은 공통 연결 설정이며, 파일을 수정하는 것만으로 GitHub의 저장소 연결·자동 추가·필드가 구성되지는 않는다.
연결 시 URL의 owner·number·실제 Project ID·접근 권한을 확인하고, API 작업은 그 대상과 현재 저장소 범위로 제한한다. 제목 검색의 첫 결과를 선택하지 않는다.

권장 Project 필드는 아래와 같다. 개인 소유 Project에서도 사용할 수 있는 custom field로 정의하며 조직 전용 Issue type에 의존하지 않는다.

| 필드 | 역할·권장 값 |
|---|---|
| Status | Backlog / Ready / In progress / In review / Blocked / Done |
| Priority | P0 / P1 / P2. 이유는 Issue에 기록 |
| Kind | Research / Decision / Implementation / Verification / Release / Operations |
| 기본 필드 | Repository, Assignees, Milestone, Parent issue, Linked pull requests |

상위 Issue는 기능 묶음·단계의 범위와 하위 Issue를 연결한다. 하위 Issue는 독립적으로 수용·검증할 수 있는 작업 단위다.
관련 Q 게이트·ADR·설계 절·선행 Issue는 Issue 본문에 링크한다. 외부 저장소의 작업은 의존 링크로 참조하며 이동·복제하지 않는다.
Backlog, Ready, In progress, In review, Blocked와 출시 묶음을 보는 view부터 시작한다. 상태와 같은 의미의 라벨을 별도로 복제하지 않는다.
`needs-human`은 실제 제품 선택·권한 등의 사용자 판단이 필요한 경우에만 사용한다. 모든 의존성 대기를 사용자 승인 요청으로 바꾸지 않는다.

### 상태를 바꾸는 기준

| 상태 | 진입·종료 기준 |
|---|---|
| Backlog | 조사·결정·구현 후보. 미결정 범위를 명시 |
| Ready | 목적·범위·수용 조건·검증법·관련 결정·선행 작업이 준비됨. 구현 Issue의 핵심 제품 결정이 미정이면 Ready로 두지 않음 |
| In progress | 담당 실행과 branch/worktree를 정하고 실제 착수. 다른 작성자의 활성 작업·PR이 없는지 확인 |
| In review | 검토할 산출물·diff·검증 결과가 있고 별도 리뷰 진행 중. 미실행 검증도 명시 |
| Blocked | 구체적 원인·선행 작업·해제 조건·다음 담당자를 기록. 로컬에는 중단된 phase를 보존 |
| Done | 해당 종류의 수용 조건 충족. 구현은 병합 및 필요한 검증·문서 반영, 조사·결정은 결과 채택/처분과 영속 기록 완료 |

PR을 열었다는 이유로 Issue를 Done으로 옮기지 않는다. 닫힌 Issue도 완료·보류·중복·취소 이유를 구별한다.
조사 결과의 제안, Accepted ADR, 구현·검증 완료를 별개의 상태로 기록한다. 에이전트가 권장한 안을 사용자 승인으로 간주하지 않는다.

## 4. Issue에서 PR까지

1. **선택:** 정확한 Project의 Ready Issue와 의존성을 확인한다. 현재 요청이 특정 Issue를 지정하면 그 범위를 우선한다.
2. **조사:** 관련 코드·현재 계약·결정·기존 PR을 확인하고 필요한 재현을 수행한다. 관찰과 추정을 나눈다.
3. **계획·설계:** 수정 범위·검증·완료 조건을 Issue에 연결한다. 저장 형식·공개 API·호환성·의존 경계의 결정은 ADR로 남긴다.
4. **구현:** Issue별 branch/worktree를 사용한다. 여러 독립 Issue의 diff나 다른 작성자의 기존 변경을 섞지 않는다.
5. **검증:** 변경에 맞는 테스트·lint·문서 검사·실행 확인을 수행하고 코드 기준과 증거를 남긴다.
6. **리뷰·수정:** 구현과 리뷰를 별도 패스로 수행한다. 리뷰 횟수와 지적 처리는 아래 [리뷰 예산](#리뷰-예산)을 따른다. 독립 리뷰를 확보하지 못하면 미실행으로 남기고 독립 리뷰 통과로 쓰지 않는다.
7. **PR:** 문제와 변경 후 동작, Issue·ADR, 검증 결과, 남은 한계를 기재한다. Draft PR은 공유 가능한 중간 상태이며 완료를 뜻하지 않는다.
8. **종료:** 승인된 범위에서 병합·수용 결과를 확인한 뒤 Issue·Project·관련 문서를 맞춘다. 릴리스는 별도 Release Issue의 게이트로 진행한다.

세션 개수·모델 종류는 작업의 완료 단위가 아니다. 세션 하나가 여러 Issue를 다루더라도 각 branch·상태·증거·handoff는 분리한다.
일반적인 구현 선택과 승인 범위 안의 수정은 자율적으로 수행한다. 새 제품 결정·되돌리기 어려운 변경·실제 권한 부족이 생긴 작업만 분리해 해결한다.
승인된 계획(#1의 하위 Issue와 #1에 기록한 흐름·순서) 안의 다음 Issue는 선행 Issue(Blocked by, 다른 흐름 포함)가 모두 병합되고 선행 결정이 채택되면 지시를 기다리지 않고 착수한다(2026-10-03 소유자 결정).
선행이 남은 Issue는 #1에 적은 범위(예: 설계)까지만 먼저 진행한다. 조사·설계·구현·검증·리뷰·PR은 자율로 진행한다.
병합은 조건을 채우면 에이전트가 한다. 결정 PR은 2026-10-05, 구현·문서·운영 PR은 2026-10-06 소유자 결정이다([#54](https://github.com/handochan/aelix-analytics/issues/54)).
조건은 [리뷰 예산](#리뷰-예산)대로 리뷰를 받고 지적마다 처분(수정·한계·후속 Issue)을 PR에 남겼으며, CI가 통과했고, 열린 소유자 질문이 없는 것이다.
병합 순서는 #1과 ADR(예: ADR-0005 결정 7)을 따른다. 운영 규칙 자체를 바꾸는 PR은 병합 전에 바뀐 규칙을 소유자에게 한 줄로 알린다. 병합한 내용은 소유자에게 요약해 보고한다.
릴리스·외부 게시, 새 제품 결정·계약 충돌·되돌리기 어려운 변경·범위 변경은 소유자 확인을 받는다. 사람 검증(Q6·Q17-a, 그리고 [#21](https://github.com/handochan/aelix-analytics/issues/21)의 2026-10-05 결정에 따른 Q14의 다른 독자 확인)은 소유자에게 넘긴다.
Issue 등록·코멘트·push·PR·병합은 사용자가 요청하거나 승인한 작업 범위에서 수행한다. 운영 초안을 작성했다는 사실만으로 원격 작업을 실행한 것으로 간주하지 않는다.

### 리뷰 예산

리뷰 횟수와 새 보장의 처리는 2026-10-03 소유자 결정([#39](https://github.com/handochan/aelix-analytics/issues/39))이고, 고치는 결함의 범위와 보장 좁히기는 2026-09-25 소유자 결정([#32](https://github.com/handochan/aelix-analytics/issues/32))이다.
리뷰 라운드가 끝나지 않고 범위가 늘어나는 것을 막는다. 독립 리뷰는 작성 세션과 분리된 리뷰어(별도 에이전트·세션 또는 사람)의 리뷰다. 문서만 바꾸는 PR도 일반 PR이다.

| PR | 리뷰 | 지적을 고친 뒤 |
|---|---|---|
| 일반 PR | 독립 리뷰 1회, Codex 리뷰 1회 | 영향받는 시험과 CI로 확인한다. 리뷰를 다시 받지 않는다 |
| 기록 형식·전환 PR: 저장하는 기록의 형식을 바꾸거나 새 기록 종류를 더하거나 기존 프로젝트를 변환하는 PR(예: #4의 계약 ADR, #13, 새 기록 종류를 더하는 #14·#16·#19) | 구현 전 설계 리뷰(독립 1회·Codex 1회), 구현 리뷰(독립 1회·Codex 1회). 결정만 담은 PR은 그 리뷰가 설계 리뷰다 | 수정 diff만 한 번 확인한다 |

- 고치는 결함은 기록 손실·잘못된 기록, 멈춤, main 대비 회귀, 문서의 틀린 진술이다. 심각도는 main을 기준으로 판단하고, 이전 리비전과의 차이는 회귀로 세지 않는다.
- 그 밖의 결함(minor·nit, main에도 있던 경계 사례의 결함, 리비전 사이의 trade-off)은 PR의 남은 한계나 후속 Issue로 보낸다.
- 리뷰 중 수용 조건에 없던 보장이 새로 나오면 앞 항목과 관계없이 구현 양에 따라 처리한다. 작으면 그 PR에서 구현하고, 크면 새 Issue로 등록한다.
- 같은 보장 영역에서 수정할 때마다 결함이 다시 나오면 메커니즘을 더하지 않는다. 수용 조건 밖의 보장은 좁혀 한계로 적고, 수용 조건에 있는 보장을 좁히려면 소유자 확인을 받는다.
- 리뷰가 진행되는 동안 리뷰 대상을 고치지 않는다. 리뷰 결과는 확인할 주장으로 다루고, 지적마다 처분(수정·한계·후속 Issue)을 PR에 남긴다.

## 5. 로컬 상태 저장 규약

개발 작업용 상태는 각 worktree의 `.devstate/`에 저장하고 Git에서 제외한다. 분석 제품의 `.analytics/`나 도구별 `.claude/`·`.omc/`와 섞지 않는다.

```text
.devstate/
  runs/<run-id>/
    state.json          현재 실행의 작은 재개 요약
    handoff.md          마지막 완료·다음 행동·미해결 사항
    events.jsonl        단계 전환·검증·동기화의 추가 기록
    evidence/           명령 출력·diff·필요한 스냅샷
```

`run-id`는 실행마다 새 UUID를 사용한다. 같은 Issue를 다른 도구가 이어받으면 새 run이 이전 run을 참조한다.
여러 실행이 하나의 `current.json`을 덮어쓰지 않는다. [state 예시](templates/WORK-STATE.example.json)와 [handoff 양식](templates/WORK-HANDOFF.md)을 사용한다.
한 run의 쓰기 가능한 정본은 `checkout.worktree/.devstate/runs/<run-id>/` 한 곳이다. 다른 worktree에서 이어받으면 새 run ID와 `previous_run_id`를 기록한다.
이전 state·events 전체를 같은 run ID의 활성 사본으로 복제하지 않는다. 보존용 사본이 필요하면 원본 위치·복사 시점·읽기 전용 사본임을 표시한다.
기존 두 사본의 정리는 [보류 작업 #27](https://github.com/handochan/aelix-analytics/issues/27)에서 별도로 다루며 이 규약 추가만으로 과거 파일을 이동·삭제하지 않는다.

필수 state 정보는 schema·run ID·이전 run, repository·Issue/Project URL 또는 임시 작업 ID, 도구, branch/worktree·base/HEAD, phase, checkpoint 시각,
완료한 일·다음 행동·미결정 사항, 검증 증거, GitHub 동기화 상태다. 에이전트 세션 ID는 선택 정보이며 없어도 재개할 수 있어야 한다.
phase는 `investigate / plan / design / implement / verify / review / pr / handoff`로 기록한다. 이는 Project Status와 역할이 다르다.

### state의 값과 확장 규칙

기계가 검사할 형식은 [dev-state schema](schemas/dev-state.schema.json)에 둔다. 예시의 UUID·SHA·경로는 설명용이며 실제 실행 증거가 아니다.

| `sync.state` | 의미 | 함께 만족할 조건 |
|---|---|---|
| `unconfigured` | 필요한 GitHub 연결이나 작업 대상이 아직 정해지지 않음 | 마지막 동기화 시각은 `null`. 원격 반영 완료를 뜻하지 않음 |
| `pending` | 반영할 checkpoint·PR·handoff가 남았거나 동기화에 실패함 | `pending_actions`에 남은 일을 기록. 오류는 `last_error`에 보존 |
| `synced` | 해당 checkpoint의 필요한 원격 반영을 확인함 | `pending_actions=[]`, `last_error=null`, 확인한 `last_synced_at` 존재 |

새 연결은 `unconfigured → pending`, 반영 확인은 `pending → synced`, 새 변경·실패는 `synced → pending`으로 기록한다.
로컬 테스트 통과나 API 호출 시작만으로 `synced`로 바꾸지 않는다. Project Status의 Done과도 다른 값이다.

공통 선택 필드는 `source_checkout`(다른 원본 작업 폴더의 참조), `sync.handoff_url`(확인한 원격 handoff), 검증 항목의 `argv`·`cwd`다.
추가 도구·리뷰·전달 메타데이터는 `extensions` 안에서 `review`·`delivery`·도구 이름 등의 키로 묶는다. 공통 필드의 의미를 덮어쓰지 않는다.
기존 `/1`에 이미 들어 있는 `reviewed`·`merge` 등 알 수 없는 필드는 읽고 다시 저장할 때 보존한다. 새 writer는 새 최상위 필드를 임의로 늘리지 않는다.
schema는 이 읽기 호환성 때문에 추가 필드를 허용한다. schema 통과만으로 새 writer가 확장 위치 지침까지 준수했다고 판단하지 않는다.
schema는 한 state의 구조·불변 조건을 검사한다. 실제 전이·원격 반영·작업자 소유·증거 내용의 확인은 writer와 운영 절차의 책임이다.
필수 필드·값의 의미를 바꾸거나 기존 데이터를 해석할 수 없게 만드는 변경에는 새 schema 버전과 명시적인 전환이 필요하다.

`state.json`·`handoff.md`는 임시 파일 작성 후 같은 디렉터리의 원자적 교체로 갱신한다. `events.jsonl`은 해당 run의 작성자만 추가하며 완료된 줄을 고치지 않는다.
상태 파일의 원자적 갱신은 코드·여러 파일의 트랜잭션이나 디스크 손실 방지를 뜻하지 않는다. 소스 복구에는 별도 commit·branch 보존이 필요하다.

### 언제 기록하는가

- 작업 선택·branch 생성 직후, 조사/설계/구현 단계가 완료될 때.
- 긴 테스트·빌드·이동·전환 작업을 시작하기 전과 결과를 확인한 직후.
- 중요한 결정·실패·방향 변경·사용자 답변이 생겼을 때.
- PR 제출, 담당 도구·환경 변경, 정상 종료·중단 전에.

강제 종료 때 마지막 기록이 없을 수 있으므로 재개 시 실제 diff·프로세스·원격 상태를 다시 확인한다. 로컬 파일의 `running` 값만으로 프로세스가 살아 있다고 판단하지 않는다.

### 검증 증거의 기준

명령·실행 위치·시각·종료 코드·`pass/fail/not_run`·출력 위치·검증한 소스 기준을 함께 남긴다.
clean commit이면 SHA를 사용한다. dirty 작업은 HEAD 외에 tracked diff와 검증 범위의 untracked 파일 목록·해시를 보존한다.
증거에는 그 스냅샷 경로·해시를 연결한다. 같은 HEAD여도 diff가 바뀌면 이전 검증의 적용 범위를 다시 판단한다.
문서 링크 검사를 실행/설치/실제 모델 E2E의 통과 증거로 쓰지 않는다. 실패하거나 미실행한 검증을 handoff에서 생략하지 않는다.

측정 기록(`docs/verification/`의 md+JSON)은 세 경우에만 만든다: 저장하는 기록의 형식이나 프로젝트 전환을 바꿀 때(새 기록 종류 포함), 출시 게이트(Q1–Q17 결과표), 지원 플랫폼 선언을 바꿀 때(2026-10-03 소유자 결정).
측정 기록이 있는 PR에서 측정한 커밋 뒤에 문서가 아닌 변경이 생기면 병합 전에 다시 측정하고 기록을 고친다.
측정 기록을 만들지 않는 PR의 증거는 PR 본문의 검증 표(명령·커밋·결과·CI 실행 링크)다. 코드를 고친 뒤에는 새 push로 돈 CI 실행(실행 번호·시도)으로 표를 갱신한다. CI artifact는 90일 뒤 지워지므로 이 PR들의 원시 로그는 영구 보존하지 않는다.
문서 검사는 `uv run --locked python tools/check_docs.py --output .devstate/runs/<run-id>/evidence/docs.json`으로 실행한다.
검사 도구는 Git의 tracked 파일과 ignore되지 않은 새 문서에서 대상을 고르고, `kit/labkit/template/CONTEXT.md`도 포함한다. 삭제된 대상·외부 링크의 제외 범위를 보고서에 남긴다.
보고서의 파일 목록·SHA-256·검사기 해시·명령·HEAD·dirty 정보로 실행 범위를 확인한다. 보고서 출력은 검사 입력을 덮어쓰지 않도록 Git에서 제외된 `.devstate/` 아래에 둔다.
검증을 다시 실행할 때 이전 handoff가 인용한 증거는 보존하고 새 파일명을 사용한다. `state.json` 등 다른 종류의 기존 파일을 검사 보고서로 덮어쓰는 것은 검사기가 거절한다.
이전 PR #25의 23개 검사는 템플릿 한 개를 제외한 범위였다. 새 검사의 결과로 당시 검사를 소급 24개 통과로 바꾸지 않는다.

## 6. 동시 작업과 작성자 변경

Issue 하나에는 활성 작성자 하나를 둔다. GitHub Assignee는 책임자, 실행 claim의 agent·run ID는 실제 작업자를 나타낸다.
claim에는 Issue URL·run ID·도구·branch·worktree·시작/갱신 시각과 인계 대상을 기록한다. 다른 작성자의 기존 변경·PR을 확인한 뒤 착수한다.

같은 Git checkout의 worktree들은 `git rev-parse --git-common-dir`로 찾은 공통 Git 디렉터리 아래 `agent-claims/<issue-number>/`를 잠금 위치로 사용할 수 있다.
획득은 디렉터리의 배타적 생성처럼 원자적인 동작으로 처리한다. 소유 run만 갱신·해제하며, 파일의 시각이 오래되었다는 이유만으로 삭제하지 않는다.
기존 실행의 종료·worktree·handoff를 확인한 뒤 인계한다. 현재 이 정책 파일이나 예시 JSON이 잠금 도구를 구현한 것은 아니다.

이 잠금은 같은 checkout 범위다. 다른 clone·컴퓨터·클라우드에는 효력이 없다. GitHub Project의 Status·Issue 코멘트도 원자적인 분산 잠금이 아니다.
초기에는 같은 Issue의 실행 환경을 하나로 배정하고, 다른 환경은 종료·인계 확인 뒤 이어받는다. 전역 자동 배정·동시 인수는 실제 조정기를 구현·검증한 뒤 도입한다.
별도 Issue의 병렬 작업은 독립 worktree를 사용하고, 공통 계약 변경은 선행 Issue와 병합 순서를 명시한다.
베타까지는 서로 다른 Issue 두 개를 두 흐름으로 병행한다(2026-10-03 소유자 결정). 흐름별 배정과 순서는 [#1](https://github.com/handochan/aelix-analytics/issues/1)에 기록한다.

## 7. GitHub 동기화와 인수인계

상세 터미널 기록은 로컬에 두고, 단계별 결론·관련 commit/PR·검증 결과·다음 행동은 Issue/PR에 간결한 handoff로 남긴다.
착수, 설계 결정, 검증 완료, Blocked, PR 제출, 담당자 변경, 종료가 동기화 시점이다. 매 도구 호출마다 코멘트를 만들지 않는다.
코멘트에는 run ID와 checkpoint ID를 포함해 재시도 시 기존 게시 여부를 확인한다. Project 상태·Issue 업데이트·코드 push는 각각 성공 여부를 확인한다.

네트워크·권한 문제로 동기화하지 못하면 로컬 `sync.state=pending`과 대상·작업·마지막 오류를 남긴다.
연결 복구 시 원격의 최신 상태부터 읽고 차이를 조정한다. 오래된 pending 내용을 최신 Issue에 무조건 덮어쓰거나 새 Issue를 중복 생성하지 않는다.
Project 변경 권한이 없어도 승인된 범위의 읽기·로컬 문서·검증은 계속할 수 있다. 공동 배정이 확인되지 않은 같은 Issue의 구현은 중복 착수하지 않는다.

handoff만으로 미커밋 코드가 다른 환경에 전달되지는 않는다. 전환 전에 승인된 작업 범위의 commit을 원격 branch 또는 Draft PR에 보존하고 URL·SHA를 기록한다.
아직 올릴 수 없는 변경은 패치·untracked 파일과 복구 위치를 로컬에 보존하되, 다른 환경에서 복구 가능하다고 보고하지 않는다.
자격 증명·실제 사용자 데이터·전체 개인 대화·머신의 절대 경로가 포함된 로컬 로그를 원격 handoff에 그대로 올리지 않는다.

### 새 세션의 재개 순서

1. 공통 지침과 지정한 Issue·관련 ADR·최신 handoff를 읽는다.
2. 실제 HEAD·branch·dirty 변경과 원격 branch/PR·Project 상태를 비교한다.
3. claim과 실행 환경을 확인하고, 다른 활성 작성자가 있으면 겹치는 쓰기를 시작하지 않는다.
4. 이전 검증이 현재 소스에도 적용되는지 확인하고, 바뀐 범위에 필요한 검사만 수행한다.
5. 새 run ID로 이전 run을 연결하고 가장 가까운 미완료 행동부터 계속한다.

## 8. GitHub 작업의 진입점

상위 계획은 [공식 베타 #1](https://github.com/handochan/aelix-analytics/issues/1), 운영 기준선은 [#2](https://github.com/handochan/aelix-analytics/issues/2)다.
아래는 작업 링크 색인이다. 실시간 진행 상태·우선순위는 Project에서, 범위·수용 조건·선행 관계는 각 Issue에서 관리한다.

| 묶음 | 작업 Issue | 처음 확인할 조건 |
|---|---|---|
| 운영 기반 | [정책·설계 보존 #2](https://github.com/handochan/aelix-analytics/issues/2), [CI #22](https://github.com/handochan/aelix-analytics/issues/22) | 정확한 URL·저장소, 설정 결과와 실제 강제 범위 |
| 운영 후속 | [리뷰 보완 #26](https://github.com/handochan/aelix-analytics/issues/26), [원래 폴더 동기화 #27](https://github.com/handochan/aelix-analytics/issues/27) | 재현 가능한 증거와 state 규약. 보류 작업의 실행 여부는 별도 지시 확인 |
| 설계 결정 | [전환 순서 #3](https://github.com/handochan/aelix-analytics/issues/3)([ADR-0005](decisions/0005-names-first-storage-with-the-new-writer.md)), [공통 계약 #4](https://github.com/handochan/aelix-analytics/issues/4)([ADR-0006](decisions/0006-common-record-contract.md)), [패키징 #5](https://github.com/handochan/aelix-analytics/issues/5)([ADR-0002](decisions/0002-python-support-and-platform-scope.md)), [제안 빈도 #6](https://github.com/handochan/aelix-analytics/issues/6)([ADR-0008](decisions/0008-reuse-proposal-counting-and-deterministic-always-on.md)), [줄 끝 #37](https://github.com/handochan/aelix-analytics/issues/37)([ADR-0007](decisions/0007-line-endings.md)) | ADR을 함께 적은 Issue는 채택됨. 채택한 뒤 관련 구현 Ready로 이동 |
| A0·N0-a/c | [회귀 기준 #7](https://github.com/handochan/aelix-analytics/issues/7), [최소 설치·경계 #8](https://github.com/handochan/aelix-analytics/issues/8), [명칭 #9](https://github.com/handochan/aelix-analytics/issues/9), [영어 기본값 #10](https://github.com/handochan/aelix-analytics/issues/10) | 기존 코드와 설치·분리 실행 검증 |
| B1·N0-b | [사용량 측정 #11](https://github.com/handochan/aelix-analytics/issues/11), [관계 읽기 #12](https://github.com/handochan/aelix-analytics/issues/12), [형식 전환 #13](https://github.com/handochan/aelix-analytics/issues/13), [지표·초점 #14](https://github.com/handochan/aelix-analytics/issues/14) | 계약과 정상/실패/혼합 형식 fixture |
| B2·B3 | [맥락 조회 #15](https://github.com/handochan/aelix-analytics/issues/15), [checkpoint·인계 #16](https://github.com/handochan/aelix-analytics/issues/16), [재사용 흐름 #17](https://github.com/handochan/aelix-analytics/issues/17) | 근거 회수와 요청하지 않은 추가 모델 호출 없음 |
| B4·B4-r | [관계 UI #18](https://github.com/handochan/aelix-analytics/issues/18), [Reports #19](https://github.com/handochan/aelix-analytics/issues/19) | Q4·Q6·Q16 및 사람용 작성 경로 |
| B4-s·검증·출시 | [독립 build #20](https://github.com/handochan/aelix-analytics/issues/20), [publish #21](https://github.com/handochan/aelix-analytics/issues/21)([ADR-0009](decisions/0009-first-publish-target.md)), [Q17 #23](https://github.com/handochan/aelix-analytics/issues/23), [출시 리허설 #24](https://github.com/handochan/aelix-analytics/issues/24) | 실제 대상·설치물·Q1–Q17 결과와 미실행 항목 |

최초에는 #1 아래 작업별 하위 Issue로 연결했다. 큰 작업이 구체화되면 그 Issue 아래 한 PR에서 검토 가능한 하위 작업으로 나눈다. 17개 품질 게이트마다 기능을 중복 구현하는 별도 Issue를 만들 필요는 없다.
조사·Decision Issue도 완료 조건을 가진다. 예를 들어 결정 기록·대안·채택 이유·영향 범위가 저장소에 반영되면 완료할 수 있으며, 제품 구현까지 완료했다고 표시하지 않는다.

## 9. 도입 상태와 후속 자동화

2026-09-19 도입 전 확인: 제품 저장소는 비공개이며 Issue·PR과 연결된 Project가 없었다. 사용자가 새 Project 7을 만들고 작업 대상으로 지정했다.
동일한 이름의 다른 Project는 이 운영 범위에 포함하지 않는다. 원격 연결·등록 결과는 운영 기반 Issue와 PR에서 확인한다.
Project 7에 현재 저장소를 연결하고 Status의 Blocked·Kind 필드를 구성했다. 기존 Priority와 기본 view는 유지했다.
상위 계획 포함 24개 Issue, 하위 관계·선행 의존성과 `First official beta` Milestone을 등록했다. 숫자는 도입 시점의 관측이며 현재 현황은 Project를 조회한다.
현재 저장소의 ruleset·branch protection 조회는 요금제 관련 HTTP 403을 반환했다. 보호가 설정·강제되었다고 간주하지 않는다.
공개 전환이나 요금제 변경을 이 정책의 자동 단계로 넣지 않는다. 사용할 수 있는 CI·검토 절차로 시작하고 강제 가능한 범위는 별도로 확인한다.

도입 순서는 공통 문서·양식 → 사용자 Project URL 연결 → Issue 등록·수용 조건 정리 → 수동 작업 흐름 검증 → CI·Project 동기화 자동화다.
자동화는 저장소 범위를 제한한 Issue 추가·PR 상태 반영·재동기화부터 시작한다. 무인 작업 선택·코드 실행·merge는 별도 운영 결정과 검증이 필요하다.
승인된 계획 안의 다음 Issue 착수(2026-10-03 결정)와 조건을 채운 PR의 병합(2026-10-05·06 결정)은 §4대로 허용했다. 그 밖의 무인 작업 선택과 merge는 여전히 별도 결정이 필요하다.
문서가 전달하는 규칙, 에이전트가 수행한 절차, 실제 서버 측 강제를 구별해 보고한다.

참고: [GitHub Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects),
[Issue 의존성](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies),
[Issue Form 규격](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms).
