# Issue20 PR3 backend — 실제 사본과 기록 결박

[Issue20](https://github.com/handochan/aelix-analytics/issues/20), [Accepted ADR14](../decisions/0014-publication-record-contract.md)의
실제 build backend 검증이다. 측정 소스는 `6866c92b3840ef2c2b3a7bc6c0eb80f69deed74e`, 기준 main은
`d9413a7af9982a20028581419ab4d016b3ba3991`이다. 수치·입력 파일 해시 정본은 [JSON](2026-10-19-issue-20-publication-build-pr3.json)이다.
kit0.24는 실제 병합 순서에서 다시 확인할 후보이며 Reader format은6이다. **Workspace publication 표시와 최종 구현 리뷰는 미완료다.**

## 실제 실행과 바이트

`PYTHONPATH=src python tools/publication_build_eval.py --out <새 디렉터리>`를 clean 측정 소스에서 정상 종료했다.
작은 합성 프로젝트의 read/evidence 각각 두 build, preview 무쓰기, 별도 새 ID/manifest 해시와 동일 content 해시,
결박 seq/기준의 단조성, 제외 canary 부재, 같은 폴더의 두 ZIP 바이트 일치와 결박 검증을 확인했다. **21 assertions PASS**다.
read는10파일/16,796바이트, manifest2,870바이트, 이벤트1,366바이트이고 evidence는10파일/16,899바이트,
manifest2,874바이트, 이벤트1,374바이트였다. 이벤트 크기는 LF 포함이다. 이 fixture에는 run/figure가 없으며 코드·그림·Plotly provenance는 실제 runner 회귀 시험으로 따로 검증한다.
관측 시간은 M규모·성능 게이트·사용자 프로젝트 수용으로 쓰지 않는다. 사용자 데이터·모델·토큰 측정은 사용하지 않았다.

## 계약과 실패 사례

C1–C13의 출처·판정/범위 구별·evidence 포함 관계·완료 후보·과거 근거·결정성·무I/O·조용한 대체 거절·복사 역할·주입·밖의 이름 제거·닫힘을
선언한 project 능력에서 검증했다. C11은 기본 구현을 무장한 실제 saved build에서 주입 ID/rules와 manifest·이벤트를 대조한다.
Report/collection의 C5/C6/C9/C11 등 해당 부분은 PR4 미실행이며, 지원 능력 skip을 통과로 합치지 않는다.
새 manifest 가족·completion/nested paths를 명시적으로 delivery-only 분류하고 Report metadata/collection의 PR4 제외 분류를 유지한다.
기존 field/family 추가 음성 대조·canary 행렬·직렬화 후 형식/누출 검사도 실행한다.

기존 run 제외/해당 그림 포함 요청에서 raw 복합 키가 제외 ID를 노출하는 실패를 재현해 기존 page key_name으로 manifest 대응을 만들었다.
내부 identity 알고리즘·원문·private TypedRef는 바꾸지 않았다. 경고 위치 dict가 manifest schema를 어기던 실패는 공개 reason/count로 고쳤다.
재조회 중 Ctrl+C는 폴더를 보존하고 unknown/130을 유지한다. 완료 뒤 응답 유실·Ctrl+C, 읽기 실패·미래 표식, 결박 전후 프로세스 강제 종료,
동시 build, 폴더/ZIP 교체와 cleanup 소유권, 이미 기록한 ID 재사용, ZIP 실패 후 완료 보존을 검증했다.
500개 제외로50KiB를 넘는 실제 요청은 명시적으로 unbound 거절하고 폴더와 이전0.23 floor를 보존한다. 참조를 줄이거나 한도를 올리지 않는다.
초기 새 프로젝트 floor를0.24 미만으로 가정한 시험 실패는 fixture 전제 오류로 별도 보존했다.

## 검증 결과와 한계

고정 소스의 영향 시험은 **355 PASS / 62 SKIP, 21.67초**, 더 넓은 수정 중 platform 포함 배치는 **762 PASS / 67 SKIP, 190.19초**였다.
그 전 전체 로컬 시험은 **2717 PASS / 2 FAIL / 119 SKIP, 573.22초**다. 전체 실행 동안 나중에 추가한 test/tool 파일도 정적 검사에 보였으므로
clean exact full PASS로 주장하지 않는다. 두 실패는 측정 도구의 subprocess encoding 명시 누락과 기본 build 거절을 기대하던 옛 preview 시험이다.
둘을 고쳤고 영향을 받은 시험이 통과했다. 실패 로그와 별도 음성 대조는 작성자 run에 보존한다. local full 재실행은 하지 않았으며
현재 head의 전체 CI가 별도 결과를 제공해야 한다.

Host CPython3.11의 명시 build 경로4개와, 원본 프로젝트를 지운 후 no-site Python3.11 stdlib 독립 verify가 통과했다.
분석·렌더링·호스트 모듈 import와 환경 생성/기록 쓰기는 없었다. build/preview는 기존 프로젝트 환경을 사용한다.
실제 ZIP 메타데이터 검증은 OS 도구/브라우저/다른 독자 검증을 대신하지 않는다.

## 보류된 통합

Workspace publication 참조의 기록/폴더 분리 표시와 Timeline은 #100 공유 파일 인계 뒤 통합할 수용 조건이다.
`analytics_status` backend는 이 구분을 구현했지만 Workspace/i18n은 건드리지 않았다. ADR14의 첫 writer 동시 표시 요구를 면제한 것이 아니다.
구현 independent1+Codex1과 수정 diff1은 전체 범위의 한 frozen SHA를 잡을 때 실행하며 현재 **not_run**이다. 설계 리뷰는 PR105 완료분을 반복하지 않는다.
측정 시점 원격 CI는 not_run이다. 이후 정확한 현재 head·run/attempt·완료/skip·artifact 관찰은 PR/Issue checkpoint가 기록한다.
같은 SHA의 예전 green이나 다른 head 결과로 바꾸지 않는다. 병합 전 UI 통합·최종 리뷰·실제 main/버전 순서를 다시 확인해야 한다.
Report/collection PR4, browser/CSP/M규모/OS 도구/소유자 수용 PR5, publish/receipt #21은 아직 미실행이다.
