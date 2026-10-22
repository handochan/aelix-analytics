# Issue #20 PR3 — 구현 리뷰 수정 재측정

[PR #120](https://github.com/handochan/aelix-analytics/pull/120)의 frozen `e04ddbc`에서 완료한 독립1·Codex1 지적을 수정했다.
원시 수치·프로그램·명령·소스 해시는 [JSON](2026-10-23-issue-20-publication-review-fixes.json)에 있다. 기존 [10월22일 측정](2026-10-22-issue-20-publication-build-pr3.md)은 당시 코드의 증거다.

## 기준과 재측정

clean `caca19f219fa5698752c5bdbbbe2ab2bf9e14af9`에서 측정·영향 시험·Ruff·문서 검사를 실행했고 각 실행의 전후 소스 해시가 같다.
기존 `tools/publication_build_eval.py`를 새 디렉터리에서 다시 실행해 **34 assertions PASS**를 확인했다.
read/evidence의 독립 바이트·manifest/완료 결박·제외 canary·반복 내용·결정적 ZIP·Workspace 관찰을 다룬다.

I1은 같은 ReadModel·폴더 관찰을 fingerprint와 화면에 공유하고, 같은 바이트·역할·경로의 파싱만 생성 중 메모리64항목에서 재사용한다.
매번 모든 파일을 읽어 SHA를 대조한다. stat 보존 변조 감지 보장을 유지하며 기존 preserved-stat 시험은 바꾸지 않았다.

합성 finding 3,000개와 read build 10개(총 20,738,500 bytes)를 기준 코드로 만들고 같은 fixture 사본을 각 프로세스에서 측정했다.
`record+render`는 API 기록 뒤 Workspace 생성을 합친 시간이며 CLI 시작 시간은 포함하지 않는다. `if_changed`는 네 관측 모두 skipped다.

| 코드·시도 | render 초 | if_changed 초 | record+render 초 |
|---|---:|---:|---:|
| e04ddbc · 1 | 11.108 | 4.863 | 11.690 |
| e04ddbc · 2 | 11.446 | 4.811 | 11.203 |
| fixes · 1 | 1.616 | 0.655 | 1.695 |
| fixes · 2 | 1.712 | 0.628 | 1.899 |

관측은 로컬 macOS·CPython3.12의 2회이며 성능 게이트·M 규모·실제 사용자 수용이 아니다. 반복 build의 같은 내용에 대한 파싱 재사용 효과다.
서로 다른 모든 내용의 build 속도까지 일반화하지 않는다. 측정 프로그램은 JSON에 전문과 함께 보존했다.

## 검증·리뷰·한계

- publication·Workspace·writer·설치 포함 영향 시험: **531 PASS**. 새 I3의 case-insensitive FS 시험도 로컬 APFS에서 실행했다.
- 격리 Aelix0.1.0b2의 확장 정리 뒤 publication status/tool과 host import 경계: **2 PASS**.
- Ruff PASS, 문서90MD·23JSON·938 local links PASS(이 기록 추가 전).
- 작성자 수정 diff 점검 **1회 완료**. 별도 리뷰가 아니다. 독립1·Codex1은 frozen e04ddbc의 완료분을 유지하고 반복하지 않았다.

C1은 observation과 verifier의 지연 상대 import를 로딩 시 바인딩했다. C2·I4의 깊이 초과 manifest, I3의 다른 case ZIP 경로,
I5의 손상 DEFLATE/UTF8 ZIP, I6의 확정 줄 길이 거절 뒤 자기 폴더 정리에 각각 회귀시험이 있다. I2의 구현/후속 상태 문장도 바로잡았다.
중간 영향 실행의 설치2건은 작성자가 실행 중 core 문서 문자열을 바꿔 kit SHA가 달라진 실패였다(529PASS/2FAIL).
그 실행과 존재하지 않는 시험 경로를 지정한 exit4는 보존하며, clean 재실행의 531PASS와 구별한다.

I7–I9는 [현재 계약 D3](../CONTRACTS.md#d3-사적-공유-build와-완료-결박-kit-0260-20-pr3)의 한계로 기록했다.
기존 gitignore 누락 경고, 프로젝트/aelix 안의 수신 ZIP bytes-only 흐름, seq 불명 손작성 줄의 표시 사유 정합화가 후속 범위다.
격리 Aelix0.1.0b2 전체 통합 **997 PASS**도 clean 같은 코드에서 확인했다.
수정 후 로컬 전체·새 exact-head 3OS CI 결과는 PR 본문과 run evidence에 별도로 연결한다.
브라우저/CSP/키보드/모바일/OS ZIP/다른 독자/소유자 수용·실제 모델/토큰은 not_run이다. Report/collection 공유는 PR4, publish/receipt는 #21이다.
병합·릴리스·force-push는 하지 않는다.
