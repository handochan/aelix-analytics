# 기록 계약 예제 (ADR-0006)

[ADR-0006](../../../docs/decisions/0006-common-record-contract.md)의 reader가 프로젝트를 있는 그대로 읽을 때 내야 할
판정을 담은 합성 프로젝트 여섯 개다. 기록은 [기록 schema](../../../docs/schemas/analytics-records.schema.json)를, 각
`expected.json`은 [expected.schema.json](expected.schema.json)을 따른다. 아직 어떤 kit도 이 가족을 쓰지 않는다. #12의
reader와 #13의 writer가 이 사례로 시험한다.

| 사례 | 담은 것 |
|---|---|
| `normal` | 정확한 근거, 이름으로 받아 추론 결박한 참조(`resolved`이지만 확정 연결이 아님), basis가 `inferred`·`unverified`인 `author` 참조, 결박하지 않은 참조(`key`·`token`), 정정, 철회한 뒤 정정한 결정, 다시 연 질문, checkpoint, 완료한 Report 버전 둘, 경로와 해시로 찾은 생산자. 쓰기: 한국어 제목과 경로순이 아닌 `files`의 저장, 거절할 저장(경로·이름), 상태 변경, PEP 440 버전 비교 |
| `interrupted` | 프로세스가 사라진 running 기록, 완료 이벤트가 없는 최종 run, 이름을 바꾼 뒤 완료 이벤트 전에 멈춘 Report 버전 둘(하나는 이어 쓸 수 있고, 하나는 그사이 다른 저장이 끝났다), prepared staging, 끊긴 마지막 journal 줄 |
| `missing` | 지운 run, 없는 Visualization id, 덮어쓴 파생 표, 지운 코드 스냅샷, 없는 이벤트, 해시 없이 기록한 입력, 대상 기록이 남긴 적 없는 digest 둘, content 파일을 지운 Report 버전 |
| `conflict` | 한 발견의 두 정정, head가 아닌 이전 버전 위의 저장, 같은 `request_id`의 다른 지문, 기록이 그 상태가 아닐 때의 상태 변경(요청으로, 그리고 확인 없이 쓴 줄로), 일부만 아는 기준(`context_version: null`) |
| `mixed-version` | 한 seq 공간의 `lab.event/1` 줄(CRLF)과 `analytics.event/2` 줄(LF), `/1` writer가 끊긴 줄 뒤에 붙여 쓴 줄과 같은 seq를 다시 받은 줄, 옛 참조, 같은 경로·해시의 두 생산자, 다른 run의 스냅샷 경로, 두 스크립트에 있는 Visualization 이름 |
| `future-version` | 더 새로운 writer가 남길 수 있는 것: 모르는 가족 버전·kind·참조 type·가족, 모르는 버전의 run 기록, 모르는 형식의 기록을 가리키는 참조, 모르는 선택 필드, 더 새로운 writer를 요구하는 표식 |

## 규칙

- 각 사례는 #13 전환 뒤의 프로젝트 루트다: `analytics.json`(`analytics.project/2`)과 `journal.jsonl`. run은 `lab.run/1`로
  남는다. 다음 run 가족은 #13이 정한다. `mixed-version`의 `lab.event/1` 줄과 `.lab/context/`는 전환 전의 기록이다. 옛
  context 버전을 어디에 둘지는 ADR-0005 결정 5에 따라 #13이 정할 배치이고(`docs/issue-3-transition-order` 병합 뒤 링크),
  reader는 `.analytics/context/`와 `.lab/context/`를 모두 찾는다(ADR-0006 4절).
- `expected.json`은 사례를 있는 그대로 읽은 결과다. `writes`의 항목은 차례가 아니라 지금의 사례에 대한 각각의 시도다.
  `result`는 `saved`(새 기록), `existing`(같은 요청이 이미 끝나 그 결과를 돌려줌), `resumed`(이름을 바꾼 뒤 멈춘 버전에
  완료 이벤트만 더함), 또는 kit이 돌려주는 오류(`request_conflict`, `revision_conflict`, `state_conflict`, `writer_too_old`,
  `report_path_invalid`, `name_not_portable`)다. 거절할 저장 요청에는 `request_fingerprint`가 없다.
- `references[].from`은 이벤트 id(`e6`)이거나 manifest의 `based_on.refs`인 `report:<report_id>/<revision_id>`다. `ref`는
  `analytics.event/2` refs 배열의 `{"index": i}`이거나 `lab.event/1` refs 항목의 `{"field": f, "value": v}`다.
- `confirmed`는 ADR-0006 3절을 따른다: `author` 결박, `observed`·`declared` basis, `resolved`.
- 후보는 Visualization이면 `<run_id>/<visualization_id>`, 생산자면 run id, 같은 id의 이벤트면 `journal:<줄>`이다.
- `journal.damaged`는 파싱할 수 없는 줄이고, `journal.recovered`는 그 가운데 ADR-0006 6절의 규칙으로 기록을 복구한 줄이다.
- `records[].mismatched_updates`는 재생에서 상태에 적용하지 않은 `record_update`다(ADR-0006 6절).
- `unsupported.items[].location`은 `journal:<줄>`이거나 기록 파일의 경로다.
- 요청 지문은 ADR-0006 8절의 정규 JSON(RFC 8785)의 sha256이다. [fingerprint-vectors.json](fingerprint-vectors.json)에
  한국어 제목, 경로순이 아닌 `files`, escape할 문자의 정규 문자열과 sha256이 있다.
- ID·시각·`env` 값은 합성이다. `interrupted`의 running 기록이 가진 pid 4294967295는 Linux·macOS·Windows의 어느 프로세스도
  갖지 않는다.
- 이 디렉터리의 `.gitattributes`는 `* -text`다. 해시가 기대값의 일부이고, `mixed-version/journal.jsonl`은 `lab.event/1`
  줄의 CRLF(Windows의 kit이 쓴 그대로)와 `analytics.event/2` 줄의 LF를 함께 지닌다(ADR-0007).
- 사례 안의 Markdown 파일에는 링크가 없다. 문서 검사가 그 파일도 읽는다.

## 검사

[test_record_contract.py](../../test_record_contract.py)는 schema, schema에 대한 모든 기록, 바이트에 대한 해시와 지문을
확인하고, 참조의 해석 상태·상태 재생·Report 저장과 재시도를 ADR-0006의 규칙대로 계산해 각 판정과 비교한다. reader는
아니다. 사례를 바꾸면 같은 커밋에서 해시를 고친다. 맞지 않는 해시는 시험이 알려 준다.

```sh
uv run --locked pytest -q tests/test_record_contract.py
```
