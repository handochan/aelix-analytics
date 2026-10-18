# Issue19 PR2 — Report metadata·부분 묶음·형식별 보기 검증

[PR118](https://github.com/handochan/aelix-analytics/pull/118), [Issue19](https://github.com/handochan/aelix-analytics/issues/19),
[Accepted ADR-0013](../decisions/0013-report-storage-and-collections.md) PR2 범위다.
측정 소스는 `04798848f400e34cdb44e0ff036cb1f86ac8908d`(kit0.22.0), 기준 main은 PR117을 병합한
`bd41995e1208ead40eb774bf9200b0cc274e78d3`이다. 수치·명령·해시의 정본은 [JSON](2026-10-19-issue-19-report-collections-pr2.json)이다.
그 뒤 검증 문서·CHANGELOG/PR 링크만 추가하며 제품 코드가 바뀌면 다시 측정한다.

## 실행과 보존

`PYTHONPATH=src python tools/report_collection_eval.py --out <새 증거 디렉터리>`로 새 합성 프로젝트에서
한국어·결합 문자·CRLF 파일, 유한 소수 JSON과 알 수 없는 선택 값을 등록했다. 20개 요청의 18complete/1failed/1not_generated를
목록에서 계산하고 실패분을 추가한 새 묶음은 19complete/0failed/1not_generated다. 같은 요청은 existing이며
새 묶음·반복 렌더 뒤 옛 manifest·metadata sidecar·본문·구성원 주소의 SHA-256은 변하지 않았다.
생성 fixture(리터럴 파일 작성), 저장, metadata 조회, 전체 파일 확인, 렌더의 비용을 별도로 기록했다.
실제 모델/외부 생성기나 일반적인 지연 보장으로 해석하지 않는다. 토큰은 미측정이다.

선택 값은 `_analytics/metadata.json`의 결정적 UTF-8 바이트로 기존 지문에 결박하며 기존 ADR-0006 지문 벡터와
기록 fixture는 고치지 않았다. 모든 선택 필드 변경·unknown 필드 보존·sidecar 불일치·비유한/비JSON 입력 거절,
완료/준비/중단/손상 구성원과 rename 뒤 재시도, 혼합 상태의 원래 구성원 위치를 시험했다.

ReportRenderer 교체 계약은 실제 StaticReportRenderer와 boundary-recording double에 적용했다. double은 형식 파싱을
실제 adapter에 위임하므로 독립 Markdown 파서 구현을 입증하는 시험은 아니다. 선택 bytes/entry/명시 경로 대응만 전달하고
프로젝트 파일·네트워크 조회를 막았다. Markdown raw HTML·외부/미대응 자원·경로 탈출·경로 충돌·Unicode 대응,
HTML/SVG/PDF/Office/미지원 형식·이미지 다운로드 fallback을 확인했다. 지원 판정은 publication 공유 적합성 판정이 아니다.

## 리뷰와 시험

설계 PR106의 독립1·Codex1·수정diff1은 채택 전에 완료했으며 반복하지 않았다.
구현은 frozen `fc17349a0115899d0ca535256463b6a7530c09fa`에서 독립1·별도Codex1을 마쳤다.
고유 P2 두 건은 complete만 추린 참조의 index를 원래 collection.members index로 표시한 오류,
complete의 선택 reason 타입/빈 문자열 검사가 schema보다 느슨한 오류다.
`0479884`에서 원래 위치와 공통 reason 검사를 고치고 혼합 상태 및 잘못된 reason 5종/유효 reason 회귀를 더했다.
수정 diff 1회는 PASS이며 원래 payload 재현과 7개 회귀 시험을 통과했다. 추가 전체 리뷰는 하지 않았다.

수정 전 전체 시험은 **2549 PASS / 1 FAIL / 122 SKIP**, 568.96초·정상 종료1이다.
유일한 실패는 새 시험 write_text 4곳에서 명시 UTF-8을 빠뜨린 정적 검사였고 수정했다. 이 실패를 성공으로 바꾸지 않는다.
수정한 clean `0479884`의 영향 시험은 **592 PASS / 0 FAIL / 63 SKIP / 8 deselected**, 50.59초·종료0이다.
초기 작업 트리 158PASS/3SKIP와 더 넓힌 495PASS/2FAIL도 로컬 evidence에 보존했다. 두 실패는 새 Report schema 필드를
게시 범위의 닫힌 분류표에 반영하도록 요구한 검사였으며 deferred_PR4로 분류했다. Report 공유 능력을 열지 않았다.

stdlib CPython3.11.15의 SDK/CLI metadata·부분 묶음·같은 요청·원본 바이트 보존도 PASS다.
분석/host/rendering 모듈 import가 없음을 확인했다. Markdown rendering 자체는 분석 프로젝트 환경의 parser를 사용하며
stdlib 저장·조회 지원을 rendering 의존성 지원과 합치지 않는다. ruff·공백 검사와 문서83MD/19JSON/888links(이 문서 추가 전)도 PASS다.

## 최종 CI와 남은 범위

이 측정 시점의 최종 세 OS CI는 아직 실행 전이다. **병합 전 확인한 정확한 최종 head·실행 번호·attempt·완료 결과의
인수증은 [PR118 검증 표](https://github.com/handochan/aelix-analytics/pull/118)와 연결 Issue checkpoint에 기록한다.**
이 JSON의 not_run을 최종 성공으로 바꾸거나 앞선 SHA의 green을 새 SHA 결과로 취급하지 않는다. expected skipped는 별도 표시한다.

Reports Workspace 화면 조립은 PR3다. 실제 모델/생성기·브라우저/모바일/키보드/소유자 수용과 Report publication 공유 적합성은
이 PR2에서 실행하지 않았다. 기록 완료·파일 무결성·작성자의 content_status·구성원 구조·결론 검토를 하나로 합치지 않는다.
