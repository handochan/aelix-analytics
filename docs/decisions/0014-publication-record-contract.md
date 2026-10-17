# ADR-0014: 공유 build의 기록 기준·결박·참조

Date: 2026-10-17

Status: Proposed — [#20](https://github.com/handochan/aelix-analytics/issues/20) PR-0의 설계 리뷰 대상.
OQ1–OQ25의 제품 선택은 [2026-10-07 소유자 결정](https://github.com/handochan/aelix-analytics/issues/20#issuecomment-6038725925)이다.
이 ADR은 그 선택의 기록 형식을 구체화하며, 채택과 구현·검증 완료를 구별한다.

## 맥락과 현재 코드

[ADR-0006](0006-common-record-contract.md)는 `publication`·`publish_receipt` kind와 `publication` 참조를 예약했고,
[ADR-0009](0009-first-publish-target.md)는 manifest 해시와 프로젝트 이벤트의 결박을 게시의 전제로 정했다.
[공유 build 설계](../design/PUBLICATION-BUILD.md)의 OQ15는 새 `content_seq` 필드,
OQ20은 보존 파일을 검사하는 일반 참조 규칙의 예외이므로 구현 전에 결정으로 남긴다.

`a57ee7b`의 `relations.KINDS`·`REF_TYPES`는 이름을 알지만 `ReadModel._resolve`는 `publication`을
`unsupported`로 돌려준다. `Journal.as_of_seq`는 파싱·복구한 줄의 최대 seq를 포함한다. `writer.KIT_KINDS`에는
실제 publication writer가 없고, `publishing/`·`analytics build`도 없다. 기록 schema는 kind를 예약하고
`publication`·`publish_receipt`의 필수 `details.publication_id`와 참조의 `publication_id`를 검사한다.
새로 구체화하는 결박 필드들과 manifest는 아직 검증하지 않는다. 이 PR은 runtime·schema·kit 버전·표식을 바꾸지 않는다.

## 결정

### 1. 불변 정체성과 두 해시

- `publication_id`는 `pub-` 뒤에 소문자 Crockford base32 ULID 26자를 붙인다:
  `^pub-[0-9a-hjkmnp-tv-z]{26}$`. 시각은 정렬 기준이 아니고, build 순서는 결박 이벤트의 journal seq다.
- 새 manifest 가족은 `analytics.publication/1`이고 파일 이름은 `publication.json`이다.
  `package_sha256`은 **그 파일의 최종 바이트**의 SHA-256이다. manifest 안에 자기 자신의 해시를 넣지 않는다.
- `content_sha256`은 `files[]`에서 정체성 파일 세 개(`publication.json`, `about.html`, `assets/identity.js`)를
  제외한 `{path, bytes, sha256}` 목록을 `path` 코드 포인트 순으로 정렬한 RFC 8785 JSON 바이트의 SHA-256이다.
  manifest 자신은 애초 `files[]`에도 없다. 비교할 때 `renderer`가 같은지 먼저 확인한다.
- 같은 기록·선택·프로필·renderer와 같은 원본 바이트로 다시 build하면 새 ID와 패키지 해시를 얻어도 내용 파일과
  내용 해시는 같다. Report 내용 버전이나 분석 결론을 새 버전으로 만들지 않는다.

### 2. `as_of_seq`와 `content_seq` — OQ15

`basis.as_of_seq`는 ADR-0006 §5의 뜻을 유지한다. CONTEXT 동기화 뒤 일관된 읽기가 본 journal의 마지막 seq이며,
이 build의 결박 이벤트보다 앞이다. build가 읽을 수 없으면 결박하지 않는다. 새 build의 값은 null이 아닌 0 이상의 정수다.

`basis.content_seq`는 그 기준 이하에서 **게시 계열로 안전하게 제외할 수 없는 마지막 줄의 seq**다.
게시 계열의 닫힌 집합은 지금 `analytics.event/2`의 `publication`·`publish_receipt`뿐이다.
알고 있는 schema·kind의 정상 이벤트만 제외한다. 모르는 가족·버전·kind, `unsupported` 줄, 복구된 줄은
kind 철자가 같아도 제외하지 않는다. reader가 파싱·복구하여 얻은 seq를 사용하고, seq를 얻지 못한 손상 줄에
새 seq나 시각을 추정하지 않는다. 해당 줄의 손상 표시는 공유 모델에 그대로 남긴다.

- 후보가 없으면 `content_seq=0`, `content_recorded_at=null`이다. 그 밖에는 `0 <= content_seq <= as_of_seq`다.
- `basis.content_recorded_at`은 그 내용 기준 이벤트에 기록된 시각이다. 없거나 유일하게 정할 수 없으면 null이다.
- `basis.context_version`은 기준 이하 마지막 `context` 이벤트의 버전 이름이며, 없거나 모르면 null이다.
  두 seq 사이에는 게시 계열만 있으므로 내용 기준에서도 같은 버전이다.
- 게시 계열은 공유 투영·상태 재생·내용 파일에 넣지 않는다. #21이 새 게시 시작 kind를 정하면 닫힌 집합과 schema·시험을
  함께 고친다. 이름 접두어나 문자열에 `publish`가 든다는 이유로 자동 제외하지 않는다.
- `as_of_seq`·ID·build 시각은 위 세 정체성 파일에만 둔다. 내용 파일의 기준 띠는 `content_seq`·기록 시각·CONTEXT 버전을 쓴다.
  `content_seq`는 build 순서·cursor·Report의 작성 근거 기준을 대신하지 않는다.

예: 내용 e410 뒤 e411·e412가 정상 `publication`이면 새 build는 `as_of_seq=412`, `content_seq=410`이다.
그 뒤 e413이 모르는 kind면 다음 내용 기준은 413이다. 새 내용이 없다고 추정하지 않는다.

### 3. `publication` 완료 이벤트

기존 `analytics.event/2`의 공통 필드(`schema`, `seq`, `id`, `recorded_at`, `kind`, `actor`, `text`, `refs`)를
유지하고 `kind=publication`을 쓴다. 출처는 기존 `Origin` 계약을 따르며 사람의 명시적 build를 system으로 바꾸지 않는다.
새 writer의 필수 `details`는 다음과 같다. 기존 선택 필드의 뜻이나 이벤트 가족 버전은 바꾸지 않는다.

| 필드 | 계약 |
|---|---|
| `publication_id` | 결정 1의 ID |
| `format` | `analytics.publication/1` |
| `package_sha256`, `content_sha256` | 결정 1의 소문자 64자리 SHA-256 |
| `profile` | `{id: read 또는 evidence, rules_version: 1}` |
| `scope` | `project` 또는 `reports`. 선택 Report 버전은 manifest에 보존 |
| `basis` | `as_of_seq`, `content_seq`, `context_version`을 모두 포함. 앞 두 값은 결정 2, CONTEXT 없음은 null |
| `analysis_state` | `{value: in_progress 또는 complete, basis: declared}`. 실행·기록·분석 검사·결론 검토와 별개 |
| `exclusions` | 요청한 제외 대상의 정확한 참조 목록. 없으면 빈 목록. 패키지에는 ID나 이름을 내보내지 않음 |
| `location` | `.analytics/publications/<publication_id>`인 프로젝트 상대 디렉터리. 절대 경로나 외부 ZIP 경로는 없음 |
| `checks` | `format: 1`, `result: pass`, `inspection`의 `inspected`·`pinned_scanned`·`not_inspectable` 개수 |

`refs`는 포함한 Report의 정확한 버전(`includes`, `declared`, `author`, manifest digest)과 기준 CONTEXT 버전
(`includes`, `observed`, `author`, 보존 파일 digest)만 담는다. 없으면 빈 목록이다. 모든 run·그림의 참조를 이벤트에
복제하지 않는다. 항목별 포함 사실은 manifest의 `included`·`files[].sources`에 있다.

manifest와 결박 이벤트의 ID·형식·두 해시·프로필·범위 종류·분석 상태·검사 요약은 일치해야 한다.
공통 기준 세 값(`as_of_seq`·`content_seq`·`context_version`)도 일치하고 `content_recorded_at`은 manifest에 추가로 남긴다.
실제 writer의 details 검증과 schema·정상/음성 예제는 PR-3에서 함께 구현한다. 이 필드를 갖추지 못한 손작성 줄을
정상 결박으로 인정하지 않으며, 모르는 manifest 형식의 참조를 `resolved`로 올리지 않는다.

### 4. manifest의 필수 계약

| 필드 | 계약 |
|---|---|
| `schema`, `publication_id`, `built_at`, `entry` | `analytics.publication/1`, 결정 1의 ID, offset 포함 시각, `index.html` |
| `basis` | 결정 2의 `as_of_seq`·`content_seq`·`content_recorded_at`·`context_version` 모두 포함 |
| `analysis_state`, `profile`, `ui_locale` | 결정 3의 상태·프로필, `en` |
| `scope` | `kind`(`project`·`reports`)와 선택한 `reports` 목록. 목록은 정확한 `report_id`·`revision_id`·Version n·기준 시점 상태·manifest SHA-256·파일 대응 `{path, sha256, package_path}` |
| `included`, `not_included` | 포함된 `ItemKey` 목록, 범주·사유별 제외 개수. 제외 ID·이름은 없음 |
| `reader_features` | 실제 제공한 `read`·`relations`·`downloads`·`interactive_figures`만. P0에는 `search` 없음 |
| `renderer` | kit 판·패키지 형식 판·번들 라이브러리 이름·판·해시·라이선스 |
| `remote_links`, `limits`, `checks` | 실제 원격 이동 링크, 검사/지원 한계, 검사기 판·수행 결과·검사 상태별 개수 |
| `content_sha256` | 결정 1의 내용 해시 |
| `files` | manifest 자신을 뺀 모든 파일의 `path`·`bytes`·`sha256`·`origin`·판정한 `format`·`inspection`. 사본은 `sources`, 가공본은 `derived_from` |

`scope.kind`는 이벤트의 `details.scope`와 같다. Report 선택이 없으면 `scope.reports=[]`다.
파일 경로는 패키지 안의 상대 경로이고 중복·대소문자/정규화 충돌·경로 이탈·symlink를 허용하지 않는다.
기록 해시가 있는 사본은 대조에 통과한 바이트만 넣고 `verified: recorded_hash`로 표시한다. 기록 해시가 없는 사본은
`verified: no_recorded_hash`로 구별하며 과거 바이트와 일치한다고 보장하지 않는다. kit 생성물은 `verified: kit_generated`다.
복사 중 변경 검사는 어느 사본에도 적용한다. `ItemKey`·`sources`·검사 상태와
누출 판정의 세부 구조는 [build 설계 §4·§7·§11](../design/PUBLICATION-BUILD.md)에 따른다.
`project_id`·프로젝트 경로·기기·사용자·`source_revision`은 manifest에 넣지 않는다.
Report 묶음 선택은 #19가 채택한 묶음 버전 계약을 PR-4가 따르며 임시 저장 형식을 먼저 만들지 않는다.

### 5. 결박과 실패

폴더는 `.analytics/publications/<publication_id>/`에 새로 만들고 파일 → 검사 → manifest(마지막 파일) → 완료 이벤트 순서로 쓴다.
한 프로젝트의 build는 읽기부터 결박까지 기존 플랫폼 잠금으로 직렬화한다. 표식 허용 판정은 잠금·폴더 등 첫 부수 효과보다 앞이다.
첫 publication 쓰기 전 writer floor를 올리며 실제 kit 번호는 PR-3 시점의 main과 조정한다. 이 ADR은 번호를 선점하지 않는다.

- 이벤트가 커밋 지점이다. 폴더·manifest만 있으면 `recording_status=incomplete`이고 #21은 게시하지 않는다.
- 결박 단계 시작 뒤 예외·중단이면 journal 잠금 안에서 같은 ID·패키지 해시의 이벤트를 다시 읽는다. 있으면 완료,
  없으면 `publication_not_recorded`/`binding=unbound`, 다시 읽지 못하면 `binding=unknown`이다. 폴더는 삭제하지 않는다.
- 결박 단계 전 실패는 자기 폴더 삭제를 시도하고 실패 위치를 알린다. 자동으로 다른 build 폴더를 정리하지 않는다.
- build 재시도는 새 ID의 새 build다. `request_id` 기반 저장 재시도 보장을 추가하지 않는다.
- ZIP 실패는 이미 끝난 build의 결박을 되돌리지 않는다. 외부 전달·최신 주소·전달 재시도는 #21과 ADR-0009가 맡는다.

### 6. `publication` 참조 판정 — OQ20, ADR-0006 §4의 예외

공통 type·relation·basis·version_binding 검사와 참조 기록의 역사적 경계를 먼저 따른다. 그 경계 이전의
`details.publication_id`가 일치하는 완료 이벤트를 찾는다. 참조 digest는 그 이벤트의 `package_sha256`이다.

| 관찰 | 해석 |
|---|---|
| 대상 이벤트 없음 | `missing` / `record_absent` |
| 같은 ID의 후보 이벤트가 여럿 | `ambiguous`. 마지막 이벤트를 고르지 않음 |
| 대상의 형식·필수 결박을 해석할 수 없음 | `unsupported`. 이벤트를 보존하고 성공으로 취급하지 않음 |
| 참조 digest가 결박된 패키지 해시와 다르거나 없음 | `missing` / `digest_mismatch` |
| 하나의 알려진 완료 이벤트와 digest가 일치 | `resolved` |

build 폴더는 보존 정본이 아니라 전달 준비물이므로 이 판정에서 파일 존재·내용을 검사하지 않는다.
폴더를 지우거나 바꿔도 같은 결박 참조는 `resolved`다. `resolved`는 **기록된 패키지 정체성을 찾았음**이고 지금의 패키지 무결성이나
게시 성공을 보장하지 않는다. 다른 참조 type의 파일 비교 규칙은 바꾸지 않는다.

`--list`와 Workspace·`analytics_status`의 publication 참조 표시는 기록 상태(`complete`·`incomplete`와 사유)와
관측한 폴더 상태(`same`·`changed`·`missing`)를 함께 보인다. 폴더는 manifest·결박·실제 파일을 `verify_package`로 대조한다.
파일 검사를 하지 않은 응답은 확인하지 않았음을 명시한다. 이 표시·reader 변경은 PR-3 범위이며 설계 채택만으로 활성화되지 않는다.

## 대안과 영향

`as_of_seq`에서 게시 이벤트를 빼면 공통 reader와 같은 필드가 다른 값을 갖는다. 원래 뜻을 유지하고 `content_seq`를 더한다.
폴더를 참조 정본으로 보면 전달을 마치고 폴더를 지운 정상 기록도 missing이 된다. 대신 결박 정체성과 폴더 검사를 나란히 표시한다.
패키지 해시 하나만 비교하면 ID·시각 때문에 같은 내용의 반복 build도 달라진다. 정체성과 내용의 두 해시를 구별한다.

기존 프로젝트 전환과 기존 kind의 뜻 변경은 없다. 새 기록의 첫 writer는 schema·예제·floor·reader·표시를 함께 구현해야 한다.
계약 문서의 구현 주장은 PR-3에서만 갱신하며, 현재 미지원 동작을 통과로 적지 않는다.

## 구현 순서와 확인할 것

[2026-10-17 순서 완화](https://github.com/handochan/aelix-analytics/issues/20#issuecomment-6232441606)에 따라
PR-0~PR-3은 #19 앞에 시작할 수 있다. #13·#18은 이미 병합됐다. Report 범위 PR-4는 #19 뒤이고,
#21 구현은 #20 PR-3 뒤부터 시작한다. 공통 ADR·`relations.py`는 먼저 병합한 계약을 뒤 PR이 반영한다.

1. PR-1: 같은 내용 반복 build, 정상 publication/receipt만 제외하는 닫힌 집합, unknown·unsupported·복구 줄·빈 journal·시각 미확인 시험.
2. PR-2: 투영·파일·링크·누출 검사와 무쓰기 preview. 이 단계는 결박을 남기지 않는다.
3. PR-3: schema·예제·writer floor, manifest/이벤트 일치, 실패 뒤 다시 읽기, 중복 ID, 역사적 참조 경계, digest 불일치,
   폴더 삭제·변조 후 참조와 폴더 상태의 구별, marker gate·계층·LF 규칙을 시험하고 기록 형식 측정(md+JSON)을 남긴다.
4. PR-4: #19의 정확한 Report 버전·근거·현재 선택·철회·renderer를 대조한다. 새 필드는 공유 허용 여부를 정하기 전 기본 제외다.
5. PR-5: ZIP·독자 환경 Q14 검증. 실제 독자 확인·외부 게시·릴리스는 수행한 사실과 미실행 범위를 별도로 남긴다.

PR-0의 문서 검사·설계 리뷰는 runtime·브라우저·지원 플랫폼·사람 검증의 통과 증거가 아니다.
