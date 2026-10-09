# 분석 자산 — #17 구현 계약

작성: 2026-10-09 · 상태: Proposed, 구현 전 설계

[ADR-0010](../decisions/0010-analysis-assets-and-suggestion-records.md)의 구체 계약이다.
[ADR-0006](../decisions/0006-common-record-contract.md)의 버전·참조·공통 writer,
[ADR-0008](../decisions/0008-reuse-proposal-counting-and-deterministic-always-on.md)의 제안 정책을 따른다.
아래 명령과 필드는 아직 현재 기능이 아니다. 기본 영어 표시는 Analysis assets / Asset candidate / Asset suggestion이다.

## 1. 공개 표면과 구현 경계

새 상시 모델 도구를 만들지 않는다. 기존 `analytics_record`의 선택적 `details`가 후보·검사·효과 기록을 받으며,
새 kinds와 짧은 schema만 추가한다. 상세 계약과 예시는 필요할 때 파일에서 읽는다. 모델이 이름을 골라 호출할 때의 Q11 비용을 잰다.
자산 상세 조회·명시적 검토·상태 변경·등록은 기존 CLI를 bash로 호출할 수 있다.
SDK와 kit CLI는 같은 응용 기능을 사용한다. 사람 명령 `/analytics assets ...`는 같은 kit 호출의 얇은 adapter다.

| 동작 | kit CLI | SDK(분석 프로세스 밖에서도 stdlib로 동작) |
|---|---|---|
| 기록 | `analytics record asset_candidate TEXT --details-json JSON`, 같은 형태의 `asset_check`·`asset_effect` | `analytics.record(kind, text, refs=..., details=...)` |
| 목록·상세 | `analytics assets list`, `analytics assets show ASSET_ID` | `analytics.assets(asset_id=None)` |
| 요청 시 검토 | `analytics assets review [ASSET_ID]` | `analytics.review_assets(asset_id=None)` |
| 승격·거절·보류 | `analytics assets update EVENT_ID --expected STATE --to STATE --details-json JSON --reason TEXT` | `analytics.update_asset(event_id, expected=..., new=..., details=..., text=...)` |
| 명시적 등록 | `analytics assets register EVENT_ID --name NAME` | `analytics.register_asset(event_id, name=...)` |
| 프로젝트 모드 | `analytics assets mode on_demand\|always_on` | `analytics.set_asset_mode(mode)` |

키·경로·JSON은 shell 안에서 코드를 평가하지 않고 parser가 읽는다. JSON 입력은 객체여야 하며 최대 이벤트 바이트 규칙을 따른다.
`review`는 기존 기록을 판정하고 반환한 후보별로 `trigger=requested` 알림 이벤트를 kit이 쓴다.
빈 결과는 이벤트 없이 제안 없음으로 답한다. 목록·상세와 검토를 구별한다. 검토도 모델을 호출하지 않는다.
모델 기반 상세 검토는 사람이 대화에서 요청한 턴의 기존 모델 작업이며 확장이 새 턴/provider 호출을 시작하지 않는다.
요청 턴의 사용량은 #11 기록과 연결하고 분리 불가한 다른 작업이 있으면 그렇게 표시한다.

읽기·정책은 stdlib이고 분석 라이브러리를 import하지 않는다. 공통 writer가 필드·참조·상태·출처·표식 검증을 소유한다.
Application은 확정 근거를 reader로 읽고 ProposalPolicy에 직렬화 가능한 사실을 주며, 파일 복사와 출력은 adapter가 한다.
ProposalPolicy는 `(facts, settings) -> scores, requirements`만 바꿀 수 있다. 후보 정체성·사용자 선택·이력·창·발행은
공통 응용 기능이 소유해 대체 policy가 우회할 수 없게 한다. 신규 모듈은 실제 역할에 맞는 계층표와 `tests/test_layers.py`에 등록한다.

## 2. 기록·버전·참조

새 이벤트는 모두 `analytics.event/2`, 공통 Origin 필드와 `details.contract="analysis_asset/1"`을 쓴다.
`asset_id`는 프로젝트 안의 안정된 ASCII 이름이며 ADR-0003의 공개 이름 규칙을 따른다.
새 종류를 쓰는 kit의 최초 버전 `ASSET_WRITER_SINCE`는 구현 시 정하고, 기록 전에 표식 floor를 올린다.
알려진 옛 `reuse_*` 기록은 최소 필드로도 계속 읽는다. 새 종류의 잘못된 필드는 writer가 거절하며, reader는 외부 편집 기록을
보존하고 경고·판정 불가로 보여 주며 점수나 승격 근거로 쓰지 않는다.

| kind | 필요한 details | 참조와 의미 |
|---|---|---|
| `asset_candidate` | `asset_id`, `title`, `problem`, `artifact_kind`, `conditions`, `exclusions`, `next_check`; 선택 `input_contract`, `output_contract`, `code`, `files` | 출처 refs는 run·코드·발견. `code`는 정확한 `code_snapshot` 참조 1개. `files`는 산출물의 상대 경로와 정확한 코드/데이터 관측 참조. 불명확한 초기 후보도 기록하되 정책·승격은 확정 근거만 사용 |
| `asset_check` | `asset_id`, `candidate_event_id`, `result: passed\|failed`, `boundary: bool`, `check_scope`, `code`, `artifact_digest` | 정확한 후보 `about`, 검사 run `uses`, 검사 코드 `uses`. code와 산출물 digest는 후보 버전과 같아야 함. `result`는 작성자의 결과 선언이며 run 성공과 별개 |
| `asset_effect` | `asset_id`, `candidate_event_id`, `effect_basis: estimated\|observed`, `description`; observed이면 `authoring_cost`, `maintenance_cost`, `measurement` | 정확한 후보 `about`; observed에는 측정 근거 ref가 1개 이상 확정되어야 점수 2. 추정은 점수 1까지 |
| `asset_mode` | `mode`, `scope: project\|session`; session 범위는 `session` 필요 | 작성자가 사람인 모드 선택만 유효. 프로젝트 설정 변경의 기록이며 인증이 아님 |
| `asset_notification` | `asset_id`, `candidate_event_id`, `trigger: requested\|automatic`, `surface`, `as_of_seq`, `evidence`, `scores`, `requirements`, `policy_version` | 정확한 후보 `about`. evidence는 판정에 사용한 확정 참조와 관련 검사/효과/사람 표시의 이벤트 ID 집합. 사람이 봤다는 뜻이 아님 |

글 필드는 비어 있지 않은지와 종류만 검사하며 의미를 자동 평가하지 않는다. 초기 후보는 conditions/exclusions/next_check를 빈 문자열로
남길 수 있다. 등록·점수는 아래 요건에 따라 이를 확인한다. `code`가 없으면 candidate는 초기 제안으로만 남는다.
불명확하거나 미결박 출처를 보존하되 확정 출처로 올리지 않는다.
`files` 항목은 `{path, ref}`이며 ref는 `code_snapshot` 또는 `data_observation`이고 digest를 가진다.
모든 path는 후보 bundle 안의 상대 regular-file 경로다. bundle digest는 정렬한 `(path, sha256)`의 정규 JSON 바이트 sha256이다.
skill 본문 같은 비 Python 파일은 run의 input/output 데이터 관측과 결박하며 code_snapshot으로 가장하지 않는다.
bundle이 없거나 검사 run이 그 bundle의 파일 버전을 관측하지 않았으면 등록 검사 근거가 아니다.
사람의 반복 필요 표시는 `record_update`의 `details.demand={"repeated_need":true}`로 남기며 actor=human 기록만 점수에 쓴다.
관찰 가능한 효과는 `asset_effect`로 분리한다. 후보 문장만 수정해서 효과·검사 점수가 늘지 않는다.

후보 수정은 같은 `asset_id`의 새 `asset_candidate` 이벤트와 기존 버전의 `supersedes` 참조다.
writer는 잠금 안에서 같은 logical ID의 마지막 버전 1개를 정정하는지 확인한다. 새 버전은 `proposed`부터 시작하고
과거 검사·등록 상태는 이전 버전에 남는다. 논리 ID의 알림·거절·보류 이력은 모든 버전을 합친다.
기존 파일이나 기록을 덮어쓰지 않는다. 판정 기준에서 분기/모호한 버전 이력이 발견되면 자동 제안하지 않는다.
검사·효과 기록은 정확한 후보 버전에 속한다. 같은 코드라도 새 버전으로 옮겨 붙이지 않는다.

후보의 명시적 중복/통합은 상태를 유지하는 `record_update`에
`details.identity={"relation":"duplicate_of"\|"merged_into","target":exact_candidate_ref}`로 기록한다.
양쪽 이력을 삭제하지 않고 동일 그룹으로 셈한다. self-link·다른 프로젝트·불명확한 대상은 거절한다.
과거 후보로부터의 명시적 `derived_from`도 제안 억제에 이력을 합친다. 내용의 유사도를 자동 판정하지 않는다.

## 3. 상태와 명시적 프로젝트 등록

상태 변경은 기존 `record_update`와 `expected_state`·`new_state`, 정확한 대상 `updates` 참조다.
사람/에이전트/CLI/SDK가 같은 writer 검증을 쓴다. 일반 `record_update`로도 승격 증거 검증을 우회하지 못한다.
상태 유지 update는 demand/identity만 받을 때 허용한다. 거절·보류는 사유 항목과 그때의 evidence·scores를 kit이 저장한다.
사유 항목 없는 거절도 허용하지만 자동 재제안은 없고 명시적 검토로만 보인다.

| 전이 | 필수 증거 |
|---|---|
| `proposed -> trial` | 정확한 보존 code_snapshot, 비어 있지 않은 입력·출력 계약과 다음 검사. 기존 source run의 입력 집합을 비교 기준으로 저장 |
| `trial -> validated` | 해당 후보/코드 버전의 `passed` 검사: 성공 사례 1개, 원래 입력 집합과 다른 입력의 사례 1개, `boundary=true` 사례 1개. 하나의 검사가 여러 요건을 충족할 수 있음. 모두 확정 연결과 `status=ok` 검사 run 필요 |
| `validated -> registered` | 아래 등록 동작만 기록 가능. 임의 generic update는 `registration_required`로 거절 |
| `proposed/trial/validated -> deferred/rejected` | 사유 text; 선택적인 사유 항목 `demand`, `checks`, `scope`, `benefit`, `source`, `identity` |
| `deferred/rejected -> proposed` | 명시적 재검토이며 이전 거절 이력은 유지 |
| `registered -> deprecated` | 사유와 선택적 대체 후보 ref. 이미 등록한 파일을 자동 삭제하지 않음 |

별도 입력의 정의는 run의 확정된 input 집합 `(identity path, sha256)`이 원래 run과 다른 것이다.
기본 실행 성공은 결과 검사 통과가 아니다. 실패 검사와 unsupported/missing/changed/inferred 관계는 보존하지만 승격 근거가 아니다.

등록은 검사된 code_snapshot/데이터 관측의 파일 바이트를 후보 버전 디렉터리에 보존한 후 **명시적으로 복사**한다.
원본 후보는 `assets/candidates/<asset_id>/<event_id>/`, 코드 목적지는 `lib/<name>.py`(function/module) 또는 `lib/<name>/`(package),
skill 목적지는 프로젝트 `.aelix/skills/<name>/`다. tree의 파일은 모두 프로젝트 안의 regular files이고 symlink/특수 파일은 거절한다.
경로는 기존 platform identity/containment 포트로 판정하며 name은 공통 공개 이름 검사로 제한한다.
skill에는 UTF-8 `SKILL.md`와 사용 시점·호출·해석·검사·실패 조건을 요구하며 실제 Aelix loader 발견 시험을 기록한다.
이 항목은 skill 자체의 실행을 가장하지 않고 검사 스크립트·bundle 내용 검사·독립 loader 발견을 구별한다.
코드/skill의 내용 해시가 검사 버전과 맞지 않으면 `asset_changed`다. 이름 충돌은 덮어쓰지 않고 `registration_exists`다.
전역 위치·프로젝트 밖의 목적지와 사용자의 `~/.aelix`는 등록 대상으로 받지 않는다.

등록 결과의 `record_update.details.registration`은 원본·후보 보존 위치·목적지, 파일별 sha256, 검사 이벤트 ID,
loader 검사 결과/범위를 가진다. 등록은 내용을 실행하거나 함수 계보·보편적 타당성을 인증하지 않는다.
파일 복사와 journal 줄은 한 파일시스템 transaction이 아니다. staging 후 목적지를 배타적으로 게시하고 journal 성공 뒤
registered를 답한다. journal 실패면 `registration_incomplete`와 실제 목적지/미기록 상태를 답하고 자동 재시도·덮어쓰기를 하지 않는다.
복사된 파일만 있으면 registered로 판정하지 않는다. 명시적 재호출은 같은 후보·내용·목적지임을 확인한 뒤 남은 기록만 완료할 수 있다.
파일 복사 전후 표식·상태를 잠금 안에서 재확인한다. 기존 Writer lock과 등록 lock 순서는 등록 lock -> journal lock으로 고정한다.

## 4. 결정적 점수와 이력

ProposalPolicy의 입력 사실은 같은 `as_of_seq`로 읽은 후보/상태와 확정 참조다.
확정은 `basis in {observed,declared}` + `version_binding=author` + `resolution=resolved`다.
독립 사용은 후보의 코드 스냅샷과 같은 경로·sha256의 script/modules 관측 또는 후보 버전과 run 사이의 확정 연결,
run `status=ok`, `rerun_of` 없음, 서로 다른 `(script identity path, input identity path+sha256 집합)`이다.
동일 입력의 단순 rerun/동일 script+input run은 수요를 늘리지 않는다. 함수 단위 호출을 관측했다고 표현하지 않는다.

| 항목 | 결정적 값 |
|---|---|
| `demand` 0–3 | 독립 사용 >=2면 3, 아니면 human 반복 필요면 2, 독립 사용 1이면 1, 그 밖 0 |
| `checks` 0–3 | 해당 코드 버전의 passed 검사 중 다른 입력/경계 조건이면 3, passed 검사면 2, 원래 run·코드·finding 확정 출처만 있으면 1, 그 밖 0 |
| `scope` 0–2 | 조건·제외 조건 둘 다 있으면 2, 하나면 1, 그 밖 0 |
| `benefit` 0–2 | 비용과 함께 관측하고 확정 근거가 있는 효과면 2, 추정 효과 기록이면 1, 그 밖 0 |

필수 조건은 출처+문제+산출물, 중복/상태 허용, demand>=2/checks>=2/scope=2/benefit>=1, 이력/모드/창 허용이다.
총점 8 이상이 초기 문턱이다. `min_score`는 정수 0..10이며 필수 항목을 낮추지 않는다.
대체 policy도 같은 공유 fixture의 항목별 값·필수 결과를 내야 한다.

후보의 logical ID와 명시적 identity/derived_from으로 묶인 후보의 마지막 알림·거절·보류 기록을 기준으로 한다.
이전 `evidence`에 없던 새 확정 근거가 그때보다 한 항목을 올린 경우만 재알림한다.
거절·보류는 그때 기록한 사유 항목을 올려야 한다. 같은 근거 재참조, 문구 변경, 다른 코드 버전 검사, 단순 rerun은 해당하지 않는다.
알림/거절 이력이 있는 다른 후보 그룹의 근거 집합에 전부 포함되면 자동 억제하고 요청 검토에서는 duplicate suspicion으로 보인다.
등록·deprecated 후보는 자동 제안 대상에서 제외한다. 근거가 잘못되거나 보이지 않으면 점수를 주지 않는다.

## 5. 설정·발행·Aelix 표면

`analytics.json.asset_suggestions`는 `mode`(기본 on_demand), `min_score`(8), `cooldown_hours`(24 이상),
`measurement_targets`(input_tokens_per_review=1024, output_tokens_per_review=256)를 가진다.
숫자는 finite이고 bool은 숫자로 받지 않는다. 짧은 창/잘못된 설정은 상태 오류로 보이고 자동 발행을 멈춘다.
측정 목표는 실행 허용이나 절단 기준이 아니다. 모드 명령은 journal lock 안에서 표식 갱신+mode 이벤트를 수행하며
둘 중 하나만 기록된 실패는 명시해 자동 발행을 보수적으로 멈춘다. 모드 이벤트만 최신인 경우 재호출로 표식을 맞춘다.

Aelix의 사람 명령 `/analytics assets mode MODE --scope session`만 세션 범위를 지원한다.
같은 session 파일의 마지막 human `asset_mode`가 프로젝트 설정에 우선한다. reload/resume은 유지, new/fork은 프로젝트 모드다.
CLI는 project만 지원한다. 상시->요청 시 변경 뒤 대기 후보를 발행하지 않는다. 이력과 후보는 삭제하지 않는다.

자동 사전 판정은 새 seq/run 완료가 있거나 창 만료 뒤 첫 발행 경계에서 수행한다. cache는 재계산 가능한 진행 위치·대기 통계만 가진다.
기본 on_demand 경로는 사전 판정·후보 목록·skill 본문·검토 지침을 맥락 블록에 추가하지 않는다.
자동 발행은 사람 텍스트 status, `/analytics` 상태, 다음 에이전트 턴의 맥락 블록에서만 가능하다.
JSON status/context/모델 도구/agent-origin CLI/Workspace/query/import/run/error는 읽거나 기록만 하며 발행하지 않는다.
AssistantDone hook는 사전 판정만 하며 새 턴·모델·UI notify를 시작하지 않는다.

발행 직전 journal lock에서 현재 모드·후보·확정 근거·이력·시간을 다시 읽고 최고 점수 후보 1개를 고른다.
동점이면 기록을 replay해 최초 문턱 통과 seq가 빠른 후보, 그마저 같으면 asset_id 순서다.
`trigger=automatic` 이벤트 append가 성공한 다음 표면에 낸다. 실패 출력은 재발행하지 않고 pending notification 목록에서 볼 수 있다.
다른 프로세스도 동일 잠금/프로젝트 롤링 창을 써 동시에 많아야 1개다. 마지막 자동 이벤트 시각 이전의 현재 시각은
clock_backwards로 보이며 발행하지 않는다. 창이 끝나면 새 기록이 없어도 현재 근거로 대기 후보를 다시 판정한다.

맥락 블록은 제목·짧은 사유·근거 ID와 “Relay this suggestion; review only when the user requests it.” 한 줄만 더한다.
발행 이벤트 surface는 `aelix_context`이며 사람에게 보였다고 기록하지 않는다. 사람 status/Insights에서 미응답 알림을 읽을 수 있다.
거절/보류/등록 응답을 기록한 후보의 알림은 미응답 목록에서 빠진다. 읽기만으로 상태/창을 바꾸지 않는다.

## 6. 검증 경계와 PR 분할

B3-a: schema/reader 호환, 후보 정정·상태·검사·효과·명시적 등록, SDK/CLI/기존 record 도구와 표식 gate 행렬.
B3-b: 공용 점수 fixture, 대체 policy 계약, 새 근거/중복/거절/캐시 삭제/창/시계, 두 process 동시 발행, 텍스트와 agent/JSON 구별.
B3-c: 무장 host(provider/send_message는 호출 즉시 실패), session reload/resume/new/fork, hook와 맥락/사람 상태,
Workspace 읽기 전용 목록/상세 링크, 추가 도구 수 0과 Q11 bytes·확정 tokenizer tokens 계측.

각 구현 PR은 기록 의미의 독립+Codex 리뷰와 수정 diff 확인, 관련 pytest/lint/docs 및 Linux·Windows·macOS CI를 남긴다.
대표 합성 CLI/SDK 후보 1개에 성공·별도 입력·경계 검사 후 명시적 프로젝트 등록, 과거 버전 유지와 잘못된 승격 거절을 확인한다.
실제 모델 세션, 실제 사용자 데이터, 실사용 유효 제안률/방해 측정은 #23/#24 리허설에서 별도로 다룬다.
모델 없는 시험 통과를 실제 모델·사람 검증 통과로 바꾸지 않는다. 이 설계 PR의 검증은 문서/예시/계약 리뷰다.
