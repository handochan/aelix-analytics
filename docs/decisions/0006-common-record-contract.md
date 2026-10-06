# ADR-0006: 공통 기록 계약 — 정체성·참조·읽기 기준·저장 완료·경로 정체성

Date: 2026-10-03

Status: Accepted — 2026-10-03 소유자 결정 (Issue #4)

9절(경로 정체성)은 같은 날 [#36](https://github.com/handochan/aelix-analytics/issues/36)에 대한 소유자 결정이다.
채택은 구현 완료가 아니다. 아래 가족·필드·kind·오류 이름은 채택한 계약이며 아직 어떤 kit도 쓰거나 읽지 않는다.
현재 코드가 하는 일은 [현재 계약](../CONTRACTS.md)에 있고, 그 문서는 이 계약을 구현하는 PR(#12·#13·#19·#36)이 병합될 때
바뀐다. 기계가 검사할 형태는 [기록 schema](../schemas/analytics-records.schema.json)와
[사례별 예제](../../tests/fixtures/records/README.md)에 있다. ADR과 schema·예제가 어긋나면 ADR이 기준이고 schema·예제를 고친다.
이 계약이 기대는 형제 결정:
[ADR-0005](0005-names-first-storage-with-the-new-writer.md)(#3 전환 순서), [ADR-0008](0008-reuse-proposal-counting-and-deterministic-always-on.md)(#6 재사용 제안),
[ADR-0009](0009-first-publish-target.md)(#21 첫 게시).

## 배경

줄 번호는 `d7b6f18` 기준이다.

- 기록은 `lab.project/1`·`lab.run/1`·`lab.event/1`·`lab.dataset/1` 네 가족뿐이다(`kit/labkit/core.py:28-31`).
  정확한 대상을 가리키는 참조는 `refs.runs`·`refs.events`·`refs.context`(CONTEXT 버전 파일 이름)·`refs.resolves`(이벤트 id)다.
  `refs.charts`는 `script::chart-id` 논리 키, `refs.data`는 이름·경로다. 짧은 차트 id는 쓰는 시점의 최신 논리 키로 바뀌고
  버전은 고정되지 않는다(`core.py:376-393`). 같은 `(path, sha256)`의 생산자가 여럿이면 마지막 run이 이긴다(`core.py:591-597`).
- 읽기 기준·cursor·저장 완료·요청 재시도의 계약이 없다. run.json은 한 run에서 최대 세 번 쓰이고
  (`kit/labkit/runner.py:810·962·988`), journal 이벤트 기록이 실패하면 `finalize_errors`에 남는다. 리포트의 변경 지문은
  stat 기반이며 절대 경로를 담는다(`kit/labkit/report.py:390-409`).
- 이벤트 본문은 4,000자에서 조용히 잘린다(`core.py:400`). [용어집](../TERMINOLOGY-AND-LANGUAGE.md) 6절은 잘린 사실을 숨기지
  않는다고 정했다.
- `read_events`는 `lab.event/1`이 아닌 줄을 조용히 버리고(`core.py:434-435`), `newer_schema`는 같은 가족의 번호만
  비교한다(`core.py:425-431`). 쓰기 경로는 표식의 형식 버전을 보지 않는다. journal의 마지막 줄이 줄바꿈 없이 끊겨 있으면
  다음 이벤트가 그 줄에 붙어 함께 읽히지 않는다. 다음 seq는 읽히는 마지막 줄의 seq로 계산하므로(`_last_seq`,
  `core.py:342-353`) 그다음 이벤트가 같은 seq를 다시 받는다(이 결정을 쓰며 macOS에서 probe로 재현).
- 경로에 두 규칙이 섞여 있다. 출력은 링크를 따라가지 않는 트리 조회가 준 글자 그대로의 상대 경로이고(`runner.py:108-120`),
  입력·데이터셋은 realpath로 푼 경로이며(`core.py:133-143`), macOS에서는 입력한 철자가 그대로 남는다. #8의 측정이
  여기서 생기는 기록 손실·계보 단절 여섯 가지를 찾았다(#36).
- 설계 문서는 기능마다 ID·기준·완료 의미를 따로 제안했다([연속성 설계](../DESIGN-CONTINUITY-AND-REUSE.md) 2·6·7절,
  [보고서 설계](../REPORT-ARTIFACTS.md) 3·6절, [근거 그래프 제안](../PROPOSAL-EVIDENCE-GRAPH-2026-09-17.md) 3절,
  [지표 설계](../METRIC-DRIVEN-ANALYSIS.md) 7절, [게시 설계](../PUBLISH-AND-SHARING.md) 4·6절). #12(B1-b)·#13(B1-c)·#19가
  이 결정을 기다린다. 관련 게이트는 [베타 기준](../BETA-QUALITY-GATES.md)의 Q4·Q7·Q8·Q16이다.
- 소유자 결정(2026-10-03): 공개 이름을 먼저 바꾸고 저장 이름(표식·관리 디렉터리·schema 가족)은 #13의 새 writer와 함께
  바꿔 사용자 프로젝트를 한 번만 전환한다(#3). 이름만 준 근거 참조는 쓰는 시점의 최신 성공 버전으로 결박하되 추론
  결박으로 표시하고 확정 근거로 세지 않으며, 해시는 이미 있는 곳에만 둔다(#4). 경로 정체성은 프로젝트 상대 경로이고,
  #36의 기록 손실·계보 항목은 #13 전에 고친다(#36).

## 결정

### 1. 가족과 버전 규칙

1. 새 가족은 `analytics.project/2`(표식 `analytics.json`), `analytics.event/2`(`journal.jsonl`에 이어 쓰는 줄),
   `analytics.report_revision/1`(Report 버전 manifest)이다. 표식·관리 디렉터리(`.analytics/`)·가족 이름은 #13의 새 writer와
   함께 바뀐다(#3의 결정, [ADR-0005](0005-names-first-storage-with-the-new-writer.md)). run·dataset 기록의 다음
   가족(`analytics.run`·`analytics.dataset`)의 버전과 필드는 #13이 이 계약을 지켜 같은 전환 안에서 정한다.
2. `analytics.event/2`의 kind와 참조 type·relation은 B1–B4 전체를 지금 정한다(아래 표, 3절). #13 뒤의 기능은 선택 필드와
   새 kind·type·relation만 더한다. 기존 필드의 뜻을 바꾸는 변경은 새 가족 버전과 명시적 전환으로만 하며
   ([아키텍처](../ARCHITECTURE.md) 8절) 베타까지 하지 않는다. 사용자 프로젝트를 한 번만 전환한다는 #3의 결정이 이 규칙에 기댄다.
3. 표식의 `min_writer_version`은 그 프로젝트에 쓸 수 있는 가장 낮은 kit 버전이다. 값은 `KIT_VERSION`(배포 버전과 같다,
   ADR-0004 결정 2)과 같은 PEP 440 공개 버전의 정규형이며 epoch와 로컬 버전(`+…`)을 쓰지 않는다. 순서는 PEP 440이다
   (`0.3.0.dev1` < `0.3.0a1` < `0.3.0rc1` < `0.3.0` < `0.3.0.post1`). 그보다 낮은 kit은 쓰기를 `writer_too_old`로
   거절하고 읽기만 하며 필요한 버전을 안내한다. 이전 kit이 지키지 않는 쓰기 규칙(새 kind의 쓰기 검증 등)을 더하는 PR은 같은 PR에서
   `KIT_VERSION`을 올리고, 그 규칙을 처음 쓰는 kit은 그 쓰기 전에 표식 값을 자기 버전으로 올린다. 그래서 같은 `KIT_VERSION`의
   두 체크아웃은 같은 쓰기 규칙을 지닌다.
4. reader는 아는 가족·버전을 모두 읽는다. 아는 가족 버전은 이 계약의 세 가지와 `/1`의 네 가지이고, `analytics.run`·
   `analytics.dataset`은 #13이 버전을 정할 가족 이름이다. `/1`과 `/2`가 섞인 journal은 seq 순서로 함께 읽는다. 읽지 못하는
   기록은 조용히 건너뛰지 않고 보존하며 사유와 함께 `unsupported`로 센다: 모르는 가족(`unknown_family`), 아는 가족 이름의
   모르는 버전(`unknown_version`), 아는 버전의 모르는 kind(`unknown_kind`). 모르는 type·relation·basis·version_binding 값을
   가진 참조는 그 참조만 `unsupported`이고 기록은 읽는다. 모르는 선택 필드는 보존하고 기록을 정상으로 읽는다. 파싱할 수 없는
   줄은 줄 번호와 함께 `damaged`로 센다(복구는 6절).
5. 형식을 모르는 표식의 프로젝트에는 쓰지 않는다. `lab.json`만 있는 프로젝트와 두 표식이 함께 있는 프로젝트의 처리는
   #3의 결정과 #13을 따른다.

| 묶음 | `analytics.event/2`의 kind |
|---|---|
| `/1`에서 이어짐 | `project`, `import`, `run`, `context`, `finding`, `decision`, `assumption`, `question`, `todo`, `note`, `resolve`, `workflow_feedback`(`/1`의 `gap`) |
| B1 지표·초점·정정 | `analysis_focus`, `metric_definition`, `metric_observation`, `record_update` |
| B2 메모리 | `checkpoint` |
| B3 재사용 | `reuse_proposal`, `reuse_notification`(후보를 사람에게 보인 사실, `trigger`: `requested`·`automatic`), `reuse_mode`(제안 모드의 변경, `mode`: `on_demand`·`always_on`, `scope`: `project`·`session`). 후보의 거절·보류·승격은 그 후보를 대상으로 한 `record_update`다(6절) |
| B4 보고서·게시 | `report_revision`(Report 저장의 완료 이벤트), `publication`(게시본 build), `publish_receipt`(전달 결과) |

이벤트 줄은 `schema`, `seq`, `id`, `recorded_at`, `kind`, `actor`, `text`, `refs`(3절의 참조 목록)를 가지고,
`session`, `request_id`, `details`(kind별 필드)를 선택으로 가진다. kind별 필수 필드는 schema에 있다. 실행 수단(CLI·SDK·
도구·스크립트)의 필드는 #13이 선택 필드로 정한다([아키텍처](../ARCHITECTURE.md) 4절).

### 2. 정체성: 불변 버전 ID와 논리 키

참조할 수 있는 대상마다 바뀌지 않는 버전 ID와, 버전을 모으는 논리 키를 구별한다. 논리 키만 가리키는 참조는 버전이
결박되지 않은 참조다.

| 대상 | 버전 ID(불변) | 논리 키 |
|---|---|---|
| 이벤트(발견·결정·checkpoint 등) | `e<seq>` | — |
| run | `run_id` | — |
| Visualization 버전 | `run_id` + `visualization_id`(`/1`은 차트 `id`) | `<script path>::<visualization_id>` |
| 데이터 관측 | `run_id` + 역할(`input`·`output`) + 경로 + `sha256` | 경로 |
| 가져온 데이터 | 경로 + `sha256`(가져오기 기록) | 이름·경로 |
| 코드 스냅샷 | `run_id` + 스냅샷 경로 + `sha256` | 스크립트 경로 |
| CONTEXT 버전 | 버전 파일 이름 | `CONTEXT.md` |
| 지표 정의 | 정의 이벤트 `e<seq>` | `metric_id` |
| 분석 초점 | 초점 이벤트 `e<seq>` | — (현재 초점은 마지막 초점 이벤트) |
| 재사용 후보 | 후보 이벤트 `e<seq>` | `proposal_id` |
| Report 버전 | `report_id` + `revision_id` | `report_id` |
| 게시본 | `publication_id` | — |

- 이벤트 id는 `/1`과 `/2`가 함께 쓰는 하나의 seq 공간에서 journal 잠금 안에 배정한다(지금 `core.py:396-405`와 같다).
- `revision_id`는 시간순으로 정렬되는 무작위 ID(예: ULID)이고 kit이 저장 시도마다 새로 만든다. 화면의 Version n은 완료된
  버전의 순서에서 계산하며 주소에 쓰지 않는다.
- `report_id`는 프로젝트 안에서 유일한 이름이고 경로 구성 요소가 된다. 사람이 정하는 새 이름이므로
  [ADR-0003](0003-portable-boundaries-and-in-process-kit-loading.md) 결정 6의 규칙으로 검사한다.
- 표식의 `project_id`는 무작위 값이다. 복사한 프로젝트도 같은 값을 가지므로 계통을 알려 줄 뿐 유일성을 보장하지 않는다.
- 경로는 9절의 정체성 경로다.

### 3. 참조 형태와 버전 결박

`analytics.event/2`의 `refs`와 Report manifest의 `based_on.refs`는 같은 형태의 참조 목록이다.

```json
{"type": "visualization_version", "run_id": "20261001-091000-region-summary",
 "visualization_id": "region-revenue", "relation": "supports", "basis": "declared",
 "version_binding": "author"}
```

- `type`: `event`, `run`, `visualization_version`, `data_observation`, `dataset_import`, `code_snapshot`,
  `context_version`, `metric_definition`, `analysis_focus`, `reuse_proposal`, `report_revision`, `publication`.
  대상별 ID 필드는 2절의 표와 schema를 따른다.
- `relation`(참조하는 기록 → 대상): `supports`·`contradicts`(작성자의 주장이며 인증이 아님), `cites`(중립 인용),
  `uses`(입력·정의·코드로 사용), `derived_from`, `generated_by`, `about`(그 기록의 대상), `includes`(게시본·묶음의 구성),
  `resolves`(질문·할 일의 닫힘), `supersedes`(정정), `updates`(`record_update`의 대상).
- `basis`: `observed`·`declared`·`inferred`·`unverified`([용어집](../TERMINOLOGY-AND-LANGUAGE.md) 6절).
- `version_binding`: `author`는 기록을 쓴 쪽(사람·에이전트·스크립트·kit)이 버전까지 지정한 참조, `inferred`는 이름만
  받아 kit이 쓰는 시점에 버전을 고른 참조, `none`은 논리 키만 있는 참조다. CONTEXT 템플릿의 `<!-- pin -->`(맥락 선택
  우선순위)과 다른 개념이므로 `pin`이라는 이름을 쓰지 않는다.
- `digest`(`{"sha256": …}`)는 대상 기록이 남긴 해시를 그대로 옮긴 값이다. 사용자가 바꿀 수 있는 파일(데이터·스크립트·차트
  파일)의 쓰는 시점 바이트로 계산하지 않는다. 해시를 남기는 대상 기록은 입력·출력 관측(run.json의 `sha256`), 가져오기 기록,
  코드 스냅샷(`script.sha256`·`modules[].sha256`), Report 파일(manifest의 `files[].sha256`), 그리고 #13 뒤의 Visualization
  파일이다. 지금 캡처는 matplotlib·plotly 파일에만 해시를 계산하고 나머지 형식에는 `None`을 돌려준다
  (`kit/labkit/capture.py:335-365`). 나머지 형식에 해시를 더할지는 #13이 정한다. 대상이 그 자체로 보존 파일인 CONTEXT 버전
  파일과 Report manifest는 그 파일의 sha256이 `digest`다. writer는 대상 기록에 해시가 있으면 반드시 옮기고, 없으면 `digest`를
  두지 않는다. 모든 참조에 해시를 강제하지 않으며, 무결성을 약속하지 않는다는 [현재 계약](../CONTRACTS.md) G절은 그대로다.
- `token`은 작성자가 준 원래 이름이다. `inferred`·`none`이면 보존한다.

쓰는 시점의 결박(#13 writer):

- 같은 요청에 run이 있으면 그 run의 대상으로 결박한다(`author`).
- 이름만 있으면 쓰는 시점의 최신 성공 버전으로 결박하고 `inferred`로 표시하며 `token`을 남긴다. 최신 성공 버전은
  `execution_status=ok`이고 완료 이벤트의 seq가 가장 큰 run의 버전이다. 후보가 없거나 여러 스크립트에 같은 id가 있으면
  결박하지 않고(`none`) 후보 목록을 응답에 돌려준다.
- 결박 결과는 응답에 돌려준다. 뒤늦게 등록하는 Report의 `based_on`은 최신 상태로 채우지 않는다([보고서 설계](../REPORT-ARTIFACTS.md) 3절).
- **확정 연결**은 `version_binding=author`이고 `basis`가 `observed`·`declared`이며 해석 상태가 `resolved`인 참조뿐이다.
  `inferred` 결박은 `resolved`여도 확정 근거로 세지 않는다(Q4의 잘못된 확정 연결 0건). 확정 연결은 결론의 검토가 아니다.

읽을 때 `none` 참조는 결박하지 않는다. 후보는 그 논리 키의 버전 가운데 그 기록 전에 있던 것이다: 이벤트면 그 seq보다
앞의 완료 이벤트를 가진 버전(run의 버전이면 성공 run의 것), Report manifest면 `based_on.as_of_seq`까지(null이면 그 버전의
완료 이벤트 앞)의 버전이다. 논리 키(`key` 또는 2절 표의 논리 키 필드)가 있으면 `version_unbound`이고 그 키의 후보를 보인다.
논리 키 없이 `token`만 있으면 token에 맞는 논리 키가 둘 이상일 때 `ambiguous`, 하나일 때 `version_unbound`, 없을 때
`unknown`(`unresolved_name`)이다. 읽는 시점에 후보가 하나여도 결박하거나 확정하지 않는다.

### 4. 해석 상태

reader는 참조마다 다음 중 하나를 돌려준다. 해석은 읽기의 결과이며 기록에 쓰지 않는다.

| 상태 | 뜻 |
|---|---|
| `resolved` | 대상 기록과 보존 파일이 있고, 해시가 있는 대상이면 현재 바이트가 그 해시와 같다 |
| `content_changed` | 대상은 있으나 현재 바이트가 대상의 해시와 다르다 |
| `missing` | 대상 기록이 없다(`record_absent`), 참조의 `digest`가 대상 기록의 해시와 다르거나 해시가 없는 대상 기록에 `digest`가 있다(`digest_mismatch`: 그 버전은 기록된 적이 없다), 기록은 있으나 보존해야 할 파일이 없다(`file_absent`) |
| `version_unbound` | 논리 키 하나만 있고 버전이 없다. 추론 후보를 보여 줄 수 있으나 확정하지 않는다 |
| `ambiguous` | 이름이나 키에 맞는 대상이 여럿이고 고를 규칙이 없다. 후보를 모두 보여 준다 |
| `unknown` | 판단할 근거가 없다: 대상 기록에 해시가 없다(`no_digest`), 해석하지 못한 이름이다(`unresolved_name`) |
| `unsupported` | 이 reader가 모르는 type·relation·basis·version_binding이거나, 대상 기록이 이 reader가 모르는 가족·버전·kind다(1절 4항). 참조를 보존한다. 대상이 있으므로 `missing`이 아니다 |
| `not_included` | 게시본에서만: 원본에는 있으나 공유 범위에서 뺐다. 원본 자체의 `missing`과 구별한다 |

결박된 참조는 다음 순서로 판정하고 처음 걸린 상태를 돌려준다.

1. 참조의 type·relation·basis·version_binding을 모르면 `unsupported`.
2. 대상 기록을 찾는다. 없으면 `missing`(`record_absent`), 모르는 형식이면 `unsupported`, 같은 id의 이벤트가 여럿이면
   `ambiguous`(6절). 대상은 참조가 가리킨 run에 속해야 한다: 데이터 관측은 그 run의 `inputs`·`outputs`의 역할·경로, 코드
   스냅샷은 그 run의 `script.snapshot`·`modules[].snapshot`, Visualization은 그 run의 `charts[].id`다. 다른 run의 파일은
   바이트와 해시가 같아도 그 run의 대상이 아니다(`record_absent`).
3. 해시를 따로 남기는 대상 기록(3절)이면 참조의 `digest`가 그 해시와 같아야 한다. 다르거나, 대상 기록에 해시가 없는데
   `digest`가 있으면 `missing`(`digest_mismatch`).
4. 내용이 대상인 type(데이터 관측·가져온 데이터·코드 스냅샷)의 대상 기록에 해시가 없으면 `unknown`(`no_digest`).
5. 해시가 있으면 보존 파일의 현재 바이트와 비교한다. 파일이 없으면 `missing`(`file_absent`), 다르면 `content_changed`,
   같으면 `resolved`다. CONTEXT 버전과 Report manifest는 참조의 `digest`와 그 파일을 비교하고, Report 버전은 manifest의
   `files[]`도 같은 방식으로 본다. 해시가 없는 대상(이벤트, run, `/1`의 차트)은 기록과 보존 파일이 있으면 `resolved`이고,
   `digest`가 없는 `/1`의 `refs.context`는 그 버전 파일이 있으면 `resolved`다.

CONTEXT 버전(`context_version`, `/1`의 `refs.context`)은 버전 파일 이름으로 찾는다. reader는 `.analytics/context/`, 그다음
`.lab/context/`를 보고, 전환 기록에 대응이 있으면 그 대응을 따른다. 전환한 프로젝트의 옛 버전을 어디에 둘지는 #13이 정한다
([ADR-0005](0005-names-first-storage-with-the-new-writer.md) 결정 5).

`/1` 참조는 다음처럼 읽고, 기록은 고쳐 쓰지 않는다.

- `refs.runs`·`refs.events`·`refs.context`·`refs.resolves`는 정확한 참조다(`author`, `declared`).
- `kind=run` 이벤트의 `refs.charts`는 그 run의 버전으로 결박한다(`author`, `observed`).
- 그 밖의 `refs.charts`·`refs.data`는 `version_unbound`다. 차트는 그 이벤트 전에 있던 버전(그 이벤트의 seq보다 앞의 완료
  이벤트를 가진 성공 run의 버전)을 후보로만 보여 주고, 최신 버전이나 이벤트 직전 run으로 확정하지 않는다.
- `refs.other`는 `unknown`(`unresolved_name`)이다.
- 같은 `(path, sha256)`의 생산자가 여럿이면 생산자는 `ambiguous`이며 후보를 모두 보인다. 생산자 후보는
  `execution_status=ok`인 run이고, 소비한 run보다 늦게 시작한 run은 후보가 아니다. 시작 시각은 시간대를 반영한 시점으로
  비교한다. 지금 kit처럼 마지막 run을 고르지 않는다.

### 5. 읽기 기준

- 영속 기준은 `as_of_seq`(그 시점까지 포함한 journal seq)와 CONTEXT 버전(`context_version`)이다. 기준을 남기는 기록은 둘을
  모두 쓰고, 모르거나 없는 값은 생략하지 않고 null(미확인)로 쓴다. checkpoint의 `details`는 둘을 반드시 담고 `as_of_seq`는
  null이 아니다. Report manifest의 `based_on`은 작성자나 생성 run이 알 때만 쓰며, 쓰면 둘을 담는다(7절). 게시본의 기준은
  #20의 build manifest가 같은 두 값으로 남긴다([ADR-0009](0009-first-publish-target.md)).
- `source_revision`은 읽기가 본 원본의 싼 변경 토큰이다. journal의 크기와 마지막 seq, run.json·manifest·CONTEXT의 크기와
  mtime(필요한 곳은 해시), reader 형식 버전으로 만든 불투명 값이며 절대 경로를 담지 않는다. 같은 기계의 같은 파일에서만
  비교할 뜻이 있고 스냅샷 보장이 아니다. 목록 응답마다 돌려준다.
- cursor는 `source_revision`·질의 지문·정렬 위치를 담은 불투명 토큰이다. 기준이 바뀌면 `stale_cursor` 오류와 현재
  `source_revision`을 돌려주고 처음부터 다시 읽게 한다. journal 순서의 목록만은 `as_of_seq`로 이어 읽을 수 있다.
- 한 번의 읽기 전후 기준이 다르면 두 번까지 다시 읽고, 그래도 다르면 `updating`과 관측한 기준을 돌려준다.
- 검색·관계 인덱스는 `.analytics/cache/` 아래의 재생성 가능한 파생물이며 `source_revision`·형식·정책 버전을 기록한다.
  없거나 손상되면 파일과 ID로 직접 답하고 그 사실을 응답에 표시한다. 인덱스 기술은 #15가 측정으로 고른다.

### 6. 저장 완료와 상태 축

- 정본을 먼저 확정하고 journal의 완료 이벤트를 마지막에 쓴다. 정본은 run.json 최종본, 가져오기 기록, Report manifest와
  content다. 완료 이벤트가 그 기록의 커밋 지점이다. `/1` run의 완료 이벤트는 `refs.runs`에 그 run을 담은 `kind=run`
  이벤트이고, `/1` 가져오기의 완료 이벤트는 `refs.data`에 그 데이터셋의 경로(가져오기 기록의 `path`)를 담고 그 기록의
  `imported_at`과 같거나 뒤에 기록된 `kind=import` 이벤트다. `/2`의 `import` 이벤트는 그 가져오기 기록을 가리키는
  `dataset_import` 참조(relation `about`)를 하나 담는다.
- 정본이 있는데 완료 이벤트가 없으면 `recording_status=incomplete`와 사유로 읽는다. 완료 이벤트가 있는데 파일이 없거나
  다르면 `missing`·`content_changed`로 읽는다.
- 실행·기록·표시 상태를 따로 둔다. `execution_status`는 running·ok·error·timeout·cancelled이고, 프로세스가 사라진
  running은 읽을 때 incomplete다. `recording_status`는 complete·incomplete(사유: `not_final`, `no_completion_event`,
  `finalize_errors`)이고, Report 저장의 준비 단계는 prepared다. `render_status`는 Workspace 반영이다. 한 축의 실패가
  다른 축을 성공이나 실패로 바꾸지 않는다.
- journal에 쓰기 전에 마지막 바이트가 줄바꿈이 아니면(쓰기 도중 끊긴 줄) 줄바꿈을 먼저 쓴다. 끊긴 줄은 `damaged`로 남고
  새 이벤트는 따로 읽힌다.
- 이 규칙이 없는 `/1` writer는 끊긴 줄 뒤에 다음 이벤트를 붙여 쓴다(배경). reader는 `damaged` 줄의 처음이 아닌 위치에서
  `{"schema"`로 시작해 줄 끝까지가 JSON 객체 하나인 가장 앞의 부분을 그 줄에서 복구한 기록으로 읽고, 그 줄을 계속
  `damaged`로 센다. 같은 id의 기록이 둘 이상이면 모두 읽고 그 id를 가리키는 참조는 `ambiguous`(후보는 각 줄)다.
  새 seq는 파싱하거나 복구한 모든 줄(`unsupported` 포함)의 최대 seq 다음 값이다. 끊긴 조각에 남은 seq는 세지 않는다. 그
  쓰기는 끝나지 않아 id를 돌려준 적이 없다.
- `record_update`는 대상(`updates` 참조 하나), 기대한 이전 상태(`expected_state`), 새 상태(`new_state`), 사유(`text`)를 담는다.
  writer는 journal 잠금 안에서 아래 재생으로 대상의 지금 상태를 계산하고, 대상이 같은 프로젝트에 하나로 있으며 지금 상태가
  기대한 상태이고 아래 표가 그 전이를 허용할 때만 쓴다. 아니면 `state_conflict`로 거절하고 지금 상태와 허용하는 전이를
  돌려주며 아무것도 쓰지 않는다. 이 확인은 run 전체의 트랜잭션이 아니다.
- 기록의 상태는 journal에서 계산하며 원래 줄을 고치지 않는다. reader와 writer는 journal을 seq 순서로 한 번 재생한다.
  이벤트는 그 앞까지의 상태에 적용하고, 뒤의 이벤트가 앞에서 적용한 전이를 무효로 만들지 않는다. 대상이 하나로 정해지지
  않는 참조(`missing`·`ambiguous`)는 상태를 바꾸지 않는다.

| 대상 | 처음 상태 | 상태를 바꾸는 것 |
|---|---|---|
| 발견·결정·가정·메모·작업 피드백·지표 정의·분석 초점(`finding`·`decision`·`assumption`·`note`·`workflow_feedback`·`metric_definition`·`analysis_focus`) | `current` | 정정(`supersedes`): 서로 다른 정정 기록이 하나면 `superseded`, 둘 이상이면 `conflict`이며, 정정이 갈리면 최신 시각으로 한쪽을 고르지 않는다. 철회(`record_update`, `new_state=withdrawn`): `current`·`superseded`·`conflict`에서. 철회한 기록은 정정이 더해져도 `withdrawn`이다. 복원(`expected_state=withdrawn`): 철회가 없을 때의 상태, 곧 정정 수로 정한 `current`·`superseded`·`conflict`로만 |
| 질문·할 일(`question`·`todo`) | `open` | 닫힘(`resolves` 참조, `/1`의 `refs.resolves`): `resolved`로. 재개(`record_update`): `resolved` → `open` |
| Report 버전(`report_revision` 참조) | `current` | 철회 `current` → `withdrawn`, 복원 `withdrawn` → `current`(`record_update`) |
| 재사용 후보(`reuse_proposal`) | `proposed` | `record_update`로 한 단계씩 승격(`proposed` → `trial` → `validated` → `registered`), `registered` 전의 단계에서 `deferred`·`rejected`로, 그 둘에서 재검토로 `proposed`로, `registered`에서 `deprecated`로([연속성 설계](../DESIGN-CONTINUITY-AND-REUSE.md) 4절. 알림·횟수는 [ADR-0008](0008-reuse-proposal-counting-and-deterministic-always-on.md)) |
| 그 밖의 kind | 상태 없음 | `record_update`의 대상이 아니다 |

- `supersedes`는 첫 행의 kind만, `resolves`는 질문·할 일만 상태를 바꾼다. 다른 기록을 가리키면 관계로만 남는다(발견에 붙은
  `resolves`는 과거의 닫힘 표시이며 철회가 아니다, [연속성 설계](../DESIGN-CONTINUITY-AND-REUSE.md) 2절).
- 재생 중 기대 상태가 맞지 않거나 표에 없는 전이인 `record_update`는 상태에 적용하지 않고, 그 이벤트를 대상의 어긋난
  갱신으로 보인다. 조용히 건너뛰지 않는다. 잠금 안의 확인을 거치지 않은 쓰기나 이 표를 넓힌 더 새로운 writer의 줄에서
  생긴다. 전이를 더하는 PR은 1절 3항대로 `min_writer_version`을 올린다.

### 7. Report 저장 골격

```text
artifacts/reports/<report_id>/
  .staging/<revision_id>/    준비 중(prepared): manifest.json, content/. 저장 시도마다 새로 만든다
  <revision_id>/             저장한 버전: manifest.json(analytics.report_revision/1), content/
```

- manifest 필수 필드: `schema`, `report_id`, `revision_id`, `previous_revision`(첫 버전은 null), `request_id`,
  `request_fingerprint`, `created_at`, `actor`, `title`, `entry`, `files[]`(`content/` 기준 POSIX 상대 경로·바이트 수·
  `sha256`). `based_on`(`as_of_seq`·`context_version`·`refs`)은 작성자나 생성 run이 알 때만 쓰고, 쓰면 `as_of_seq`와
  `context_version`을 모두 담는다(모르면 null, 5절). 컬렉션·부분 생성 수·`scope`·용도 상태는 #19가 같은 버전의 선택 필드로
  더하거나 새 가족 `analytics.report_collection/1`로 더한다.
- 파일 경로(`files[].path`, `entry`)는 `content/`를 벗어나지 않는 POSIX 상대 경로다. 구성 요소는 `/`로 나누며 빈 구성 요소,
  `.`·`..`, 역슬래시, 드라이브(`C:`), 앞의 `/`를 받지 않는다. 구성 요소마다 세 OS에서 같은 파일 이름이어야 한다
  ([ADR-0003](0003-portable-boundaries-and-in-process-kit-loading.md) 결정 6의 이름 규칙: 예약 장치명, `<>:"|?*`와 제어 문자,
  끝의 `.`·공백을 받지 않는다). 경로는 NFC로 쓰고, casefold로 같아지는 두 경로를 받지 않는다(대소문자를 무시하는 파일
  시스템에서 한 파일이 된다). `entry`는 `files`의 경로 하나다. 어기면 아무것도 준비하지 않고 `report_path_invalid`로 거절한다.
  새 `report_id`가 이름 규칙을 어기면 `name_not_portable`이다(ADR-0003 결정 6과 같은 오류).
- 절차:
  1. 요청을 검사하고 새 `revision_id`로 `.staging/<revision_id>/`를 배타적으로 만든다(이미 있으면 새 ID로 다시 만든다).
     파일을 `content/`에 복사하고 복사한 바이트로 해시와 manifest를 쓴다. 복사하는 동안 원본이 바뀌면 다시 준비하거나
     실패로 남긴다. 다른 시도의 staging은 읽거나 덮어쓰거나 지우지 않는다.
  2. journal 잠금을 잡고, 아래 확인부터 완료 이벤트까지 놓지 않는다. 같은 Report의 저장한 버전(이름을 바꾼 디렉터리)에서
     같은 `request_id`(8절)의 manifest를 찾는다.
     - 있고 지문이 다르면 `request_conflict`.
     - 있고 지문이 같으며 완료 이벤트가 있으면 그 결과를 돌려준다(`existing`).
     - 있고 지문이 같으며 완료 이벤트가 없으면(이름을 바꾼 뒤 멈춘 시도) head가 그 manifest의 `previous_revision`인지
       다시 확인한다. 맞으면 manifest를 확인하고 완료 이벤트만 쓴다(`resumed`). 아니면 `revision_conflict`이며 그 버전은
       `incomplete`로 남고 head가 되지 않는다.
     - 없으면 head가 요청의 `previous_revision`인지 확인한다. 아니면 `revision_conflict`.
  3. 맞으면 이번 시도의 staging을 `<revision_id>/`로 이름을 바꾼다.
  4. manifest의 sha256을 `digest`로 담은 완료 이벤트(`kind=report_revision`, 요청의 `request_id`)를 쓰고 잠금을 놓는다
     (`saved`). 그 밖의 결과면 잠금을 놓은 뒤 이번 시도의 staging을 지운다.
- head는 완료 이벤트가 있는 마지막 버전이다. 가지를 만들지 않는다. `revision_conflict`를 받은 호출자는 최신 head를
  기준으로 새 `request_id`로 다시 저장한다(kit이 만들면 새로 만든다). head가 바뀌어 `incomplete`로 남은 버전의 `request_id`는
  그 버전의 요청에만 속하고 다른 의도의 저장으로 넘어가지 않는다(같은 값으로 다시 보내면 지문이 달라 `request_conflict`다).
- 이름을 바꾼 뒤 완료 이벤트 전에 멈춘 버전은 `incomplete`로 보이고 head가 되지 않는다. `.staging/`에 남은 것은
  `prepared`이며 버전이 아니다. 같은 `request_id`의 재시도는 남은 staging을 쓰지 않고 새로 준비한다. 남은 staging의
  정리는 자동으로 하지 않으며 명시적인 정리 명령이 맡는다(#19).
- 저장한 버전의 manifest와 content는 다시 쓰지 않는다. 검토·철회·후속 연결은 `record_update`(6절)와 새 버전으로 한다.

### 8. 재시도의 범위

- `request_id`는 모든 쓰기 envelope의 선택 필드로 예약한다. 호출자가 주지 않으면 kit이 만들어 응답에 넣는다. 호출자가
  준 값은 `[A-Za-z0-9._-]`로 된 128자 이하이며 영숫자로 시작하고 `.`으로 끝나지 않으며 예약 장치명이 아닌 이름만 받는다
  (ADR-0003 결정 6, 어기면 `name_not_portable`). 비교는 ASCII 대소문자를 구별하지 않는다. Report 저장에서 `request_id`의
  범위는 `report_id` 하나이며, 다른 Report의 같은 `request_id`는 다른 요청이다.
- 베타가 보장하는 재시도는 두 가지다.
  - Report 저장: 같은 `request_id`에 같은 지문이면 기존 결과를, 다른 지문이면 `request_conflict`를 돌려준다(7절). 요청
    지문은 `report_id`·`previous_revision`(첫 버전은 null)·`title`·`entry`·`files`(`{path, sha256}`의 목록)·`based_on`(없으면
    null)을 담은 객체의 정규 JSON을 UTF-8로 인코딩한 바이트의 sha256이다. 시각과 `request_id`는 넣지 않는다. 정규 JSON은
    RFC 8785(JCS)이고, 이 객체(ASCII 키, 정수, 문자열)에서는 다음과 같다: 키를 코드 포인트 순으로 정렬하고 공백을 넣지
    않는다. 문자열은 `"`·`\`·U+0000–U+001F만 escape하고(`\b`·`\t`·`\n`·`\f`·`\r`, 그 밖에는 소문자 `\u00xx`) 한글을 비롯한
    나머지 문자는 UTF-8 그대로 쓰며, 짝 없는 surrogate는 받지 않는다. 정수는 10진수로 쓰고 실수는 담지 않는다. `files`는
    `path`의 코드 포인트 순으로 정렬하고, 그 밖의 목록(`based_on.refs`)은 받은 순서다. 문자열을 Unicode 정규화하지 않는다
    (경로는 7절의 NFC 규칙을 이미 따른다). 같은 경로가 둘인 요청은 7절에서 거절되어 지문을 만들지 않는다. 한국어 제목,
    순서를 바꾼 `files`, escape할 문자의 정규 문자열과 sha256은 [지문 예](../../tests/fixtures/records/fingerprint-vectors.json)에 있다.
  - 게시: 단위는 `publication_id`와 대상이다. 같은 패키지·같은 대상의 재시도는 기존 결과를 재사용하고, 같은 ID의 다른
    내용은 거절한다([게시 설계](../PUBLISH-AND-SHARING.md) 6절). 게시에는 `request_id`를 요구하지 않는다. 세부와 receipt
    형식은 #21의 결정([ADR-0009](0009-first-publish-target.md))이 이 계약 위에서 정한다.
- run·일반 기록 쓰기의 멱등성은 베타 뒤 과제다([베타 기준](../BETA-QUALITY-GATES.md) 6절). 그때까지 그 쓰기는
  `request_id`를 받아 남길 뿐 중복을 막지 않는다.

### 9. 경로 정체성 (#36)

- 기록의 경로 정체성은 프로젝트 상대 POSIX 경로다. 프로젝트 밖이면 기록한 기계의 절대 경로이고, URI는 받은 그대로다.
- 푸는 순서: 절대 경로로 만든 뒤 앞에서부터 구성 요소 단위로 판정한다. 프로젝트 루트(실제 위치)에 닿기 전의 링크는 모두
  푼다. 루트 안에서 안을 가리키는 링크는 풀어 실제 위치로 기록하고, 밖을 가리키는 링크는 마운트 지점처럼 그 자리를 경계로
  삼는다. 디렉터리 링크(심볼릭 링크, junction)는 링크 아래 경로(`data/derived/x.parquet`)로, 파일 심볼릭 링크는 링크의
  경로(`data/big.parquet`)로 기록한다. 링크 뒤의 `..`는 글자로 접지 않고 푼 뒤 판정한다.
- 한 파일에 밖을 가리키는 경계 여럿으로 닿으면(같은 외부 디렉터리를 가리키는 두 링크 등) 경계의 프로젝트 상대 경로가 코드
  포인트 순으로 가장 앞인 경계 아래의 경로가 정체성이다. 경로 정체성 함수·트리 조회·입력 캡처가 이 규칙 하나를 쓴다. 그래서
  `data/b/x.csv`로 읽은 입력과 `data/a/` 아래의 출력이 같은 경로 `data/a/x.csv`가 된다. 같은 대상인지는 끝까지 푼 대상의
  `(st_dev, st_ino)`로 판정하고, `st_ino`가 0이면 끝까지 푼 경로로 판정한다.
- 철자는 파일 시스템에 저장된 이름이다. 구성 요소마다 부모 디렉터리 목록에서 같은 이름을 찾고, 없으면 NFC+casefold가 같은
  후보 가운데 `samefile`인 것의 저장된 이름을 쓴다. 목록을 읽을 수 없거나 후보가 없으면 입력한 철자를 둔다. casefold와
  `samefile`의 판정은 이미 `runner.py:663-673`·`capture.py:261-273`에 있고, 이를 목록 조회와 NFC로 넓힌다. 플랫폼 분기와
  새 포트를 두지 않는다(ADR-0003 결정 1의 네 포트 그대로). 프로젝트 루트와 신뢰 목록의 철자도 같은 규칙을 쓴다.
- 저장된 철자의 유일한 예외: kit이 관리하는 최상위 디렉터리(`runs`·`.venv`·`.lab`·`report`)가 다른 대소문자로 저장돼 있거나(`Runs`)
  링크이면 그 아래 경로는 kit의 이름(`runs/…`)으로 적는다. run 자신의 파일이 입력으로 기록되지 않고 기록이 디스크마다 같게 남도록
  ([2026-10-06 소유자 결정](https://github.com/handochan/aelix-analytics/issues/36#issuecomment-6018831863)).
- 트리 조회는 밖을 가리키는 경계로 대상마다 한 번 내려가고 위 규칙의 경로로 기록한다. 루트·조상·관리 디렉터리로 풀리는
  링크와 안을 가리키는 링크로는 내려가지 않는다. 출력 비교와 데이터셋 목록이 같은 조회를 쓴다.
- 보안 판정은 지금처럼 끝까지 푼 경로로 한다: 스크립트 위치, 재실행 포함 여부, 모듈 스냅샷 대상, 관리 디렉터리,
  `lab_query` 샌드박스(`kit/labkit/data.py:296`).
- `path`는 정체성 경로이고 `external`은 정체성이 프로젝트 밖일 때만 참이다. 밖을 가리키는 링크 경계 아래의 파일과 밖을
  가리키는 파일 링크는 `external: false`다. 필드의 뜻(프로젝트 밖인가)은 그대로이고 계산 규칙이 바뀐다. 바뀐 값은 #36 PR이
  [현재 계약](../CONTRACTS.md)에 바뀐 동작으로 적는다.
- #36 PR은 `lab.run/1`·`lab.dataset/1`의 번호를 유지하고 `KIT_VERSION`(배포 버전과 같다, ADR-0004 결정 2)을 올린다.
  #12는 `env.kit_version`으로 규칙 전후의 run 기록을 구별한다. 옛 기록은 고쳐 쓰지 않는다. #12는 루트나 링크 대상 아래의
  옛 절대 경로를 추론한 대응(`basis=inferred`)으로 읽는다.
- #36의 항목 1·2·3·5·6은 #13 전에 고친다. 6은 view 이름에서 kit 이름 규칙에 맞는 stem이 먼저 이름을 받게 하고
  대소문자만 다른 stem을 `status`에서 경고한다. 4는 신뢰 목록만 고친다: 대상이 있는 심볼릭 링크면 대상 디렉터리에서
  원자적으로 교체한다. 프로젝트 안 기록의 교체 쓰기는 지금처럼 링크 자체를 바꾸며, 이것을 의도한 규칙으로
  [현재 계약](../CONTRACTS.md) G절에 적는다. append 쓰기(journal·telemetry·queries)가 링크를 따라간다는 사실도 G절에 적고,
  막아야 하면 새 Issue로 다룬다.
- 구현은 흐름 B에서 #9-2 뒤, #13 writer 전에 병합한다. 플랫폼 강화 동결의 명시적 예외다(소유자 결정, 2026-10-03).
- #13의 전환은 기존 파일을 옮기지 않고 새 쓰기만 새 경로를 쓴다. 옮겨야 한다면 대응을 전환 기록에 남기고 #12가 그
  대응으로 읽는다.

### 10. 본문 길이와 바이트

- 새 writer는 본문을 조용히 자르지 않는다. 상한을 넘는 본문을 거절할지, 전체를 보존하고 맥락·화면에서 생략을 표시할지는
  #11의 측정으로 #13에서 정한다. 어느 쪽이든 응답에 원문 길이와 Report 안내를 준다.
- 새 기록은 UTF-8(BOM 없음)·LF로 쓴다. 해시는 저장된 바이트의 sha256이다. 사용자 내용의 사본(코드 스냅샷, CONTEXT 버전,
  Report content)은 바이트 그대로다([ADR-0007](0007-line-endings.md)).

## 대안과 선택 이유

| 대안 | 검토 결과 |
|---|---|
| Report 부분을 #19 직전 결정으로 미룸 | 승인된 완료 조건 3을 바꿔야 한다. #13 형식에 Report가 빠져 형식 판정과 구버전 쓰기 차단을 다시 설계하게 되고([보고서 설계](../REPORT-ARTIFACTS.md) 6절, [연속성 설계](../DESIGN-CONTINUITY-AND-REUSE.md) 7절), #19 앞에 결정 단계가 하나 더 생긴다 |
| #12에 필요한 읽기 계약만 먼저 | 기능마다 다른 ID·커서·완료 의미를 막는다는 #4의 목적과 반대이고, #13 앞에서 다시 결정해야 한다 |
| Report 전체 사양(컬렉션·부분 생성·`scope`)까지 지금 | #18의 탐색 경험 전에 과설계한다. 골격의 뜻을 바꾸지 않는 선택 필드로 나중에 더할 수 있다 |
| 모든 참조에 해시 필수 | 서명 없는 해시라 무결성 보장이 아닌데 그렇게 보이고, run.json이 여러 번 쓰여 무엇을 해시할지 흔들리며, `/1` 참조 대부분에는 해시가 없다 |
| 정확한 참조만 허용(이름만 준 참조 거절) | 사람의 CLI와 작은 모델에서 인자 오류와 재시도가 는다(Q11·Q13). 자유 분석을 막지 않는다는 원칙과 충돌한다 |
| 버전 결박을 `pin`으로 부름 | `<!-- pin -->`이 이미 맥락 선택 우선순위다([용어집](../TERMINOLOGY-AND-LANGUAGE.md) 6·7절). 한 이름에 두 뜻이 생긴다 |
| 게시 재시도도 `request_id`로 | 게시의 단위는 이미 만든 `publication_id`와 대상이다. 키를 하나 더 두면 같은 게시를 두 키로 판정하게 된다 |
| staging을 `request_id`로 이름 짓고 잠금 안에서 예약 | 예약 뒤 복사 전에 멈춘 예약을 풀 규칙이 따로 필요하고, 대소문자를 무시하는 파일 시스템에서 다른 `request_id`가 한 디렉터리가 된다. 시도마다 새 `revision_id`면 두 시도가 같은 디렉터리에 쓸 길이 없다 |
| 정정을 먼저 적용하고 상태 변경을 나중에 확인 | 쓸 때 확인을 통과한 철회·재개가 뒤의 정정 때문에 사라진다. 쓰기의 확인과 읽기의 계산이 같은 seq 순서 재생이어야 둘이 갈리지 않는다 |
| 같은 외부 디렉터리를 가리키는 링크마다 제 경로로 기록 | 한 파일이 경로 둘을 가져, 한 링크로 읽은 입력과 다른 링크 아래의 출력이 이어지지 않는다(#36 항목 1의 계보 단절) |
| 일반 run·기록의 멱등성을 베타에 | 요청 지문·불확실한 실행·외부 부작용의 경계가 필요하며, 베타 뒤 과제로 정해 두었다 |
| event/2의 kind를 기능마다 그때 추가 | 기능마다 형식이 바뀌고, 구버전 writer가 새 상태를 모른 채 쓰는 위험(연속성 설계 7절)이 되풀이된다. 프로젝트를 한 번만 전환한다는 #3의 결정과 어긋난다 |
| `/1`에 선택 필드만 더함 | 구버전 kit이 새 상태를 무시하고 그대로 쓴다. 지금 쓰기 경로는 표식의 형식 버전을 보지 않는다 |
| 경로 정체성을 끝까지 푼 실제 경로로 | 링크한 `data/` 아래 기록이 기계마다 다른 절대 경로가 되어 폴더 이동·다른 기기·게시에서 `missing`이 되고(근거 그래프 제안 3.1절), macOS 철자에 새 포트가 필요하다 |
| 링크는 경고만 하고 철자만 고침 | 링크한 `data/`의 출력 손실과 계보 단절이 남는다. #24는 기록 유실을 출시 장애로 둔다 |
| 경로 문제를 모두 한계로 둠 | #4가 결함 있는 키를 고정하고, #13 뒤에는 바꾸기가 더 비싸다 |
| 조용한 절단 유지 | 잘린 사실을 숨기지 않는다는 용어집 6절과 어긋나고, 잘린 기록은 되돌릴 수 없다 |

## 결과와 영향

- #12는 이 계약의 해석 상태·`/1` 읽기·혼합 버전을 [예제](../../tests/fixtures/records/README.md)로 시험한다. 예제의 기대
  판정이 reader의 적합성 기준이다. 지금의 `tests/test_record_contract.py`는 reader가 아니라 예제와 이 계약의 일치만 확인한다.
- #13은 표식·가족·kind·참조·커밋 규칙·Report 골격을 한 번의 전환에 담는다. #14–#21은 선택 필드와 새 kind만 더한다.
- 사람과 에이전트는 복합 ID를 외우지 않아도 된다. 대신 화면과 응답은 `author`와 `inferred` 결박을 구별해 보여야 한다(#18).
  추론 결박이 확정처럼 보이면 Q4 위반이다.
- 사후 변경은 해시가 있는 근거에서만 탐지된다.
- 베타의 재시도 보장은 Report 저장과 게시뿐이다. 그 밖의 쓰기를 다시 보내면 중복될 수 있다.
- Report 저장은 확인부터 완료 이벤트까지 journal 잠금을 잡으므로 그동안 다른 이벤트 쓰기가 기다린다. 복사는 잠금 밖에서
  한다. 남은 staging은 자동으로 지우지 않아 쌓일 수 있다.
- 지금 `/1` writer는 끊긴 마지막 줄 뒤에 다음 이벤트를 붙여 써 그 이벤트를 잃고, 그다음 이벤트에 같은 seq를 다시 준다(배경).
  6절의 줄바꿈 규칙은 #13 writer의 규칙이며 `/1` writer의 수정은 B1 결함 Issue
  [#49](https://github.com/handochan/aelix-analytics/issues/49)가 맡는다. 그 전에 생긴 줄은 6절의 복구 규칙으로 읽고,
  같은 seq를 받은 두 이벤트를 가리키는 참조는 `ambiguous`로 남는다.
- #36의 수정은 링크한 `data/`의 출력과 계보를 고치지만, 옛 기록과 새 기록의 경로 문자열이 달라 옛 run의 재실행이
  '입력 다름'을 보일 수 있다. 링크 대상의 절대 경로로 직접 읽은 입력은 여전히 프로젝트 밖 경로로 남는다(한계).
- `min_writer_version` 때문에 오래된 kit은 새 기능을 쓴 프로젝트를 읽기만 한다.
- [현재 계약](../CONTRACTS.md)은 지금 바꾸지 않는다. 이 결정을 구현하는 PR이 함께 바꾼다.

## 구현에서 확인할 것

1. 예제 여섯 사례의 기대 판정과 [지문 예](../../tests/fixtures/records/fingerprint-vectors.json)를 reader가 그대로 낸다(#12).
2. 모든 쓰기 진입점이 표식의 가족·버전·`min_writer_version`을 PEP 440 순서로 확인한다(#13).
3. 완료 이벤트가 없는 정본, 끊긴 마지막 줄, 이름을 바꾼 뒤의 중단, 같은 `request_id`의 재시도(대소문자만 다른 값, 동시에
   준비한 두 시도, 남은 staging), 이름을 바꾼 뒤 멈춘 시도를 그사이 끝난 다른 저장 뒤에 다시 보낸 경우(`revision_conflict`),
   `state_conflict`·`request_conflict`를 강제 중단 시험으로 확인한다(#13·#19).
4. Report 파일 경로의 거절(`..`·드라이브·예약 이름·NFC와 casefold로 같은 두 경로)과 거절할 때 staging이 생기지 않음을
   확인한다(#19).
5. 상태 재생: 철회 뒤 정정, 다시 연 질문, 잠금 밖에서 쓴 어긋난 갱신의 표시를 reader와 writer가 같게 계산한다(#12·#13).
6. 경로 정체성: 심볼릭 링크와 junction인 `data/`, `runs/`를 가리키는 junction, 밖을 가리키는 파일 심볼릭 링크, 같은 외부
   디렉터리를 가리키는 두 링크(입력과 출력이 같은 경로), 대소문자·NFD 별칭, 대소문자만 다른 데이터셋을 세 OS의 회귀
   시험으로 확인한다. 대소문자를 무시하는 파일 시스템인지는 플랫폼이 아니라 실행 중 `samefile`로 판정한다(#36).
   (#36 PR에서 확인: [측정 기록](../verification/2026-10-06-issue-36-path-identity.md). 9절의 "링크 뒤의 `..`는 글자로 접지 않는다"는
   POSIX의 의미이고, Windows는 Win32가 여는 대로 글자로 접은 경로로 판정한다. 관리 디렉터리 이름의 예외(9절)는
   [현재 계약](../CONTRACTS.md) B·E에 있다.)
7. 추론 결박이 화면·응답에서 확정 근거로 보이지 않는다(#18, Q4).

## 관련

- Issue: [#4](https://github.com/handochan/aelix-analytics/issues/4), [#36](https://github.com/handochan/aelix-analytics/issues/36),
  [#3](https://github.com/handochan/aelix-analytics/issues/3)(전환 순서, [ADR-0005](0005-names-first-storage-with-the-new-writer.md)),
  [#6](https://github.com/handochan/aelix-analytics/issues/6)(재사용 제안, [ADR-0008](0008-reuse-proposal-counting-and-deterministic-always-on.md)),
  [#12](https://github.com/handochan/aelix-analytics/issues/12), [#13](https://github.com/handochan/aelix-analytics/issues/13),
  [#19](https://github.com/handochan/aelix-analytics/issues/19),
  [#21](https://github.com/handochan/aelix-analytics/issues/21)(첫 게시, [ADR-0009](0009-first-publish-target.md)),
  [#37](https://github.com/handochan/aelix-analytics/issues/37)([ADR-0007](0007-line-endings.md), 구현 [#42](https://github.com/handochan/aelix-analytics/issues/42)).
- 설계: [연속성 설계](../DESIGN-CONTINUITY-AND-REUSE.md) 2·6·7절, [보고서 설계](../REPORT-ARTIFACTS.md) 3·6절,
  [아키텍처](../ARCHITECTURE.md) 7절, [근거 그래프 제안](../PROPOSAL-EVIDENCE-GRAPH-2026-09-17.md) 3·7절,
  [용어집](../TERMINOLOGY-AND-LANGUAGE.md) 6절.
- 형식: [기록 schema](../schemas/analytics-records.schema.json), [예제](../../tests/fixtures/records/README.md),
  [지문 예](../../tests/fixtures/records/fingerprint-vectors.json).
