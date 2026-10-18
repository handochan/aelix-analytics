# Issue19 PR1 — Report 저장·등록·버전 검증

[PR116](https://github.com/handochan/aelix-analytics/pull/116), [Issue19](https://github.com/handochan/aelix-analytics/issues/19).

최신 측정 코드는 `78f0f8ab817442e99ed81f0f3b9e732d23a4f138`(kit0.21.0), 기준 main은 #115를 병합한 `e07a0538734e102d56fa2671880c07e320d4e202`이다.
그 전 `ef850ae`의 측정·로컬 시험은 JSON의 historical 항목에 보존했다. 버전만 다른 충돌 여덟 곳을 검사해0.21.0을 유지하고 #46 코드와 CHANGELOG0.20.0을 통합했다.
수치·판정의 정본은 [JSON](2026-10-18-issue-19-report-storage-pr1.json)이다. 합성 fixture이며 실제 모델 생성·사용자 자료·화면 수용을 대신하지 않는다.

## 실행한 범위

명령은 `PYTHONPATH=src python tools/report_storage_eval.py --out <새 증거 디렉터리>`이다.
새 합성 프로젝트와 한국어/결합 문자/CRLF 본문, 1MiB 바이너리 첨부를 만들고 저장·재시도·새 버전·조회·철회/복원을 각각 측정했다.
같은 요청은 `existing`이며 v2와 두 상태 갱신 뒤 v1의 manifest·본문·첨부 SHA-256이 모두 같았다.
생성 코드를 실행하지 않았으며 목록은 본문을 읽지 않는 metadata 조회다. 토큰은 미측정이다.

실행 환경·초 단위 측정·응답 UTF-8 bytes·보존 해시는 위 JSON의 `raw_byte_measurement`에 있다.
한 합성 로컬 실행의 관측이며 보편적 지연 보장이나 생성·렌더 비용으로 일반화하지 않는다.

저장 실패 지점(복사·manifest·rename 전후·완료 이벤트 전후·끊긴 줄), 응답 손실, 같은 요청/다른 지문,
head 변경과 재개, 실제 동시 프로세스, marker floor 경합, 파일 누락/변조, active prepared 정리와 미지원 디렉터리 rename을 회귀 시험에 넣었다.
기존 지문 벡터·기록 fixture는 고치지 않았다. CLI/SDK의 작성 주체와 `analytics.reports` callable 보존도 포함한다.

## 리뷰와 시험 상태

설계 리뷰는 채택한 PR106의 독립1·Codex1이며 반복하지 않았다.
구현 리뷰는 frozen `33bf1da`에서 별도 독립1·Codex1을 실행했다. 고유 P2 네 건은 부모 경로 철자,
상태 갱신 request ID, 작성 근거 ref의 필수 target 필드, 손상된 완료 identity의 TypeError다.
각 처분과 추가한 회귀는 JSON에 있다. manifest 전 중단된 prepared 시도는 `manifest_unavailable`로 목록에 노출하는 작은 보강을 했다.
수정 diff 확인은 `271cedd`에서 규정된 한 번을 마쳤으며 PASS다. 다섯 묶음의 좁은 합성 재현으로 네 P2 처분과 prepared 가시성을 확인했다.
추가 전체 구현/설계 리뷰는 하지 않았다. `271cedd`와 위 측정 코드 사이에는 검증 문서 두 개만 다르다.

초기 frozen 전체 시험은 2,416 PASS / 3 FAIL / 113 SKIP였다. 실패는 버전 상수·배포 버전 문장·측정기 subprocess 인코딩이며 수정했다.
이전 작업 트리 batch의 실패/오류와 662 PASS / 57 SKIP 중간 성공도 별도 로컬 로그에 보존했다.
수정 영향 시험은 320 PASS였고, 통합 전 `ef850ae` runtime 코드의 전체 시험은 2,428 PASS / 113 SKIP / 0 FAIL, 535.84초로 정상 종료했다.
건너뛴 플랫폼/선택적 설치 범위를 통과로 세지 않는다. 그 뒤 차이는 검증 문서뿐이며 제품 코드·시험·설정은 동일하다.

별도 stdlib 전용 CPython3.11.15(`uv run --isolated --no-project --python 3.11`)에서도 SDK 저장·조회·철회와 CLI 동일 요청 `existing`, 원본 바이트 보존을 실행해 통과했다.
그 프로세스에는 Polars·DuckDB·matplotlib·plotly·pyarrow·Aelix host 모듈이 로드되지 않았음을 확인했다. 배포본의 최종 세 OS CI와는 별도 로컬 증거다.

## 완료된 선행 CI와 통합 검증

통합 전 exact head `dae1789`의 Linux/Windows [39124467434](https://github.com/handochan/aelix-analytics/actions/runs/39124467434)와
macOS [39124471895](https://github.com/handochan/aelix-analytics/actions/runs/39124471895), 모두 attempt1은 SUCCESS다. 실행한 작업7개 성공이며 macOS 라벨 실행의 lint1개는 skipped다.
artifact를 받아 JUnit의 실패/오류0을 확인했고 파일별 해시 목록의 SHA-256과 원래 tests/skipped 수를 JSON에 보존했다.

| 통합 전 repository JUnit | tests | skipped | failures/errors |
|---|---:|---:|---|
| Linux | 2520 | 80 | 0/0 |
| Windows | 2546 | 290 | 0/0 |
| macOS | 2520 | 82 | 0/0 |

이 선행 green을 새 main 통합 결과로 세지 않는다. 통합 코드 `78f0f8a`의 합성 저장 측정은 다시 실행해 PASS이며 전체 로컬 시험은 측정 시점에 실행 중이다.
최신 main을 포함한 최종 head의 세 OS CI는 새로 실행한다. **병합 전 확인한 최종 head·실행 번호·attempt·실제 완료 결과의 영속 인수증은
[PR116 검증 표](https://github.com/handochan/aelix-analytics/pull/116)와 연결 Issue checkpoint에 기록한다.** 이 측정 스냅샷의 pending을 최종 성공으로 바꾸지 않는다.

건너뜀·취소·진행 중인 작업을 성공으로 바꾸지 않는다. PR2의 선택 metadata·반복 묶음·ReportRenderer와 PR3의 Reports 화면,
실제 모델 생성·다른 독자의 열람·소유자 키보드/브라우저 수용·Q17 실제 인계는 이 PR1에서 실행하지 않았다.
