# Issue #20 PR3 — build 결박과 Workspace 전달 관찰 (예비 측정)

[Issue #20](https://github.com/handochan/aelix-analytics/issues/20)·[PR #120](https://github.com/handochan/aelix-analytics/pull/120),
[ADR-0014](../decisions/0014-publication-record-contract.md)의 구현이다. 수치와 소스 해시는 [JSON](2026-10-22-issue-20-publication-build-pr3.json)에 있다.

**최종 제출용 clean 측정이 아니다.** HEAD는 `ef0b44ba32fd8d843531bac86687f2909793466f`이고, 작업 파일에 main `184910c`와 kit 0.26.0·Workspace 변경을
통합한 dirty 소스를 쟀다. sandbox가 연결 worktree의 `ORIG_HEAD.lock`·`index.lock` 쓰기를 거부해 실제 merge·commit·push는 아직 없다.
JSON은 tracked diff의 SHA-256, untracked 파일과 runtime·측정 코드의 SHA-256을 남긴다. Git 권한 복구 뒤 실제 커밋에서 다시 측정해야 한다.
이전 [2026-10-19 backend 측정](2026-10-19-issue-20-publication-build-pr3.md)은 당시 범위와 실패를 그대로 보존한다.

## 측정 범위와 결과

작은 합성 프로젝트에서 read/evidence 각각 두 build, preview 무쓰기, 새 정체성과 같은 내용, 결박·기준·제외 canary·결정적 ZIP을 확인했다.
Workspace Timeline·참조 관계 페이지는 폴더가 `same`·`changed`·`missing`일 때도 기록은 `complete`, 참조는 `resolved`로 구별한다.
화면을 만드는 동안 journal이 변하지 않음과 복구한 폴더의 검사도 확인했다. **34 assertions PASS**다.

| 프로필·시도 | 파일 | 패키지 bytes | manifest bytes | 이벤트 bytes(LF 포함) |
|---|---:|---:|---:|---:|
| read · 1 | 10 | 16,796 | 2,870 | 1,366 |
| read · 2 | 10 | 16,796 | 2,870 | 1,366 |
| evidence · 1 | 10 | 16,899 | 2,874 | 1,374 |
| evidence · 2 | 10 | 16,899 | 2,874 | 1,374 |

이 fixture에는 run/figure가 없다. 그 바이트·형식·출처는 실제 runner 회귀시험의 범위로 구별한다.
관측 시간은 성능 게이트·M 규모·사용자 프로젝트 수용이 아니며 사용자 데이터·실제 모델·토큰은 쓰지 않았다.

## 실행과 실패 보존

명령은 임시 `AELIX_ANALYTICS_TRUST_FILE`을 지정하고 `AELIX_LAB_HOME`·`AELIX_LAB_TRUST_FILE`을 제거하여 실행했다.
설치 포함 전체 시험은 요청된 `AELIX_ANALYTICS_TEST_INSTALL=1 uv run --locked pytest -q -p no:cacheprovider --timeout 600 --timeout-method=thread --ignore tests/test_windows_console.py`다.
전체 시험 결과는 **2827 PASS / 17 FAIL / 89 SKIP**(1780.11초), exit `1`이며, 수정 전 소스와 원시 로그를 run에 보존했다. 수정 후 영향 시험은
**165 PASS**(21.44초), exit `0`다. 두 결과를 최종 소스 전체 통과로 합치지 않는다. 자세한 개수·중단 여부와 실패 처분은 PR/Issue checkpoint에 적는다.

출하 소스의 한국어 주석 실패는 영어로 고쳤다. 손상된 publication details가 화면을 멈추는 경로를 별도 재현하고 tolerant 표시로 고쳤다.
Git 추적 파일 digest 검사는 새 main 파일을 index에 등록하지 못한 상태의 제약이다. nested `sandbox-exec`는 최소 명령도
`sandbox_apply: Operation not permitted`(exit 71)로 실패했다. 하네스 9개 실패도 보존하며 프로세스 조회 제약이 확인됐지만, 모든 사례의 개별 근본 원인을 확정한 것은 아니다. 이 실패를 통과로 바꾸거나 원래 시험을 약화하지 않았다.

## 미실행과 남은 조건

동일 frozen commit의 구현 독립1+Codex1·수정 diff1, 새 head 3OS CI, clean 재측정은 **not_run**이다. 설계 리뷰는 PR #105 완료분을 반복하지 않는다.
브라우저·CSP·키보드·모바일·OS ZIP 도구·M 규모·다른 독자·소유자 수용, Report/collection 공유(PR4), publish/receipt(#21)는 별도 미실행이다.
PR은 Draft이며 이 기록은 Ready·병합·릴리스의 근거가 아니다.
