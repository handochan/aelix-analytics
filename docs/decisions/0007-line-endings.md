# ADR-0007: 저장소와 kit이 쓰는 텍스트의 줄 끝

Date: 2026-10-03

Status: Accepted — 2026-10-03 소유자 결정 (Issue #37)

채택은 구현 완료가 아니다. 구현은 흐름 B의 [#42](https://github.com/handochan/aelix-analytics/issues/42)가 #13의 writer 착수
전에 한다. 그때까지의 동작은 [현재 계약](../CONTRACTS.md) G절의 '줄 끝' 한계다. 기록의 바이트 규칙은
[ADR-0006](0006-common-record-contract.md) 10절과 같다.

## 배경

줄 번호는 `d7b6f18` 기준이다.

- 저장소에 `.gitattributes`가 없다. Windows 러너의 시스템 gitconfig는 `core.autocrlf=true`이고 그 체크아웃은 CRLF다
  ([Windows 인벤토리 기록](../verification/2026-10-02-issue-8-windows-inventory.md), 측정).
- kit은 기록(`kit/labkit/core.py:178-185`의 `write_json`, `207-211`의 `append_jsonl`), 템플릿 사본, 확장의 inline 스크립트,
  Workspace, kit이 만드는 차트 HTML, run 로그를 텍스트 모드로 쓴다. Windows에서는 CRLF가 된다(추론). Windows에서 기록한 run의
  출력 끝부분은 CRLF였다(같은 기록의 JSON, 측정).
- 해시는 바이트 그대로다: `env.kit_sha256`, `script.sha256`, 입력·가져온 원본·`uv.lock`의 해시(`kit/labkit/runner.py:156-168·796-800`,
  `core.py:239-244`). 같은 내용도 줄 끝이 다르면 해시가 다르다.
- macOS에서 CRLF 체크아웃을 흉내 내 빌드하자 wheel 구성원 27개 중 23개가 CRLF였고 wheel과 kit의 해시가 LF 빌드와 달랐다.
  같은 흉내에서 `* text=auto eol=lf`를 더하면 체크아웃이 모두 LF였다. Windows에서 직접 빌드하지는 않았다(조사, 2026-10-03).
- `d7b6f18`의 추적 파일 91개는 index와 작업 사본 모두 LF다(`git ls-files --eol`). 규칙을 더해도 재정규화 커밋이 필요 없다.
- 입력의 줄 끝만 바꾸면 그 입력은 `changed`가 되고, `CONTEXT.md`의 줄 끝만 바꾸면 '+0 -0 lines'인 새 맥락 버전이 생긴다.
  지금 reader는 CRLF journal을 정상으로 읽는다(조사의 probe). Python 코드는 줄 끝과 무관하게 같은 뜻으로 컴파일되지만 데이터
  파일은 바이트에 민감할 수 있다.
- 배포본은 게시하지 않아 설치본 사용자가 없다([현재 계약](../CONTRACTS.md) F절).

## 결정

1. **저장소는 LF다.** 루트 `.gitattributes`에 `* text=auto eol=lf`를 두고, 바이너리 형식(png·jpg·gif·webp·ico·parquet·
   arrow·feather·xlsx·zip·whl·gz)은 `binary`로 둔다. 바이트가 기대값인 fixture는 `-text`로 둔다(의도한 CRLF 포함).
   릴리스 wheel은 깨끗한 LF 체크아웃에서 빌드하고 `tests/test_packaging.py`가 구성원에 CRLF가 없음을 확인한다.
2. **kit이 쓰는 텍스트는 모든 플랫폼에서 UTF-8(BOM 없음)·LF다.** 기록(JSON·JSONL), 템플릿 사본과 `.python-version`,
   inline 스크립트, Workspace, kit이 만드는 차트 HTML·JSON, build stamp, 데모 파일, kit이 감싸는 run 로그가 대상이다.
   UTF-8·LF로 쓰는 헬퍼 하나를 core나 `_platform`에 두고 이 경로들이 쓴다. 플랫폼마다 다른 동작이 아니므로 포트를 만들지
   않는다([ADR-0003](0003-portable-boundaries-and-in-process-kit-loading.md) 결정 1).
3. **사용자 파일은 바이트 그대로다.** 해시는 저장된 바이트의 sha256이다. 코드 스냅샷·CONTEXT 버전·Report content·가져온
   원본의 사본은 바이트를 바꾸지 않는다. 스크립트가 직접 쓴 바이트도 바꾸지 않는다.
4. **줄 끝만 다른 변경은 `changed`에 사유를 붙인다.** 기록한 해시와 현재 해시가 다를 때만, 64 MiB 이하이고 앞 8 KiB에
   NUL이 없는 파일에 대해 CRLF→LF와 LF→CRLF로 바꾼 바이트의 해시를 계산한다. 그중 하나가 기록한 해시와 같으면 상태는
   `changed` 그대로 두고 사유 `line_endings_only`를 붙인다. status·Workspace·도구 응답은 'changed (line endings only)'로
   보이고 stale 사유에 남긴다. 새 기록 필드가 없으므로 `/1` 기록에도 적용한다. 줄 끝만 바뀐 CONTEXT 버전은 그 사실을 이벤트
   본문에 적는다('CONTEXT.md line endings changed').
5. **새 프로젝트는 git의 줄 끝 변환을 끈다.** 템플릿에 `* -text`와 그 이유를 적은 주석의 `.gitattributes`를 넣는다. 기존
   프로젝트는 고치지 않고 #13의 전환 보고서에서 제안한다. #10의 템플릿 전환과는 먼저 들어간 쪽에 맞춘다.
6. **기존 CRLF 기록은 읽기만 보장한다.** CRLF·LF·혼합을 모두 읽고 기존 파일의 줄 끝을 고쳐 쓰지 않는다. #13의 전환은 바이트
   그대로 복사한다.
7. **구현은 흐름 B의 [#42](https://github.com/handochan/aelix-analytics/issues/42) 하나다.** #13 writer 착수 전에 병합해 #13이
   LF 헬퍼를 그대로 쓰게 한다. 그 Issue가 Windows CI에서 확인할 것: ① 체크아웃한 텍스트에 CRLF가 없다(`-text` fixture 제외)
   ② 기록·템플릿 사본·inline 스크립트·Workspace의 바이트가 LF이고 고정 예제의 sha256이 Linux 레그의 상수와 같다 ③ 빌드한
   wheel 구성원에 CRLF가 없고 `kit_sha256`이 Linux 레그와 같다(artifact 비교) ④ `-text`로 둔 CRLF `lab.event/1`
   fixture(`tests/fixtures/records/mixed-version/`)를 읽는다.

## 대안과 선택 이유

| 대안 | 검토 결과 |
|---|---|
| 저장소만 LF | `.gitattributes` 하나로 wheel과 `kit_sha256`은 맞지만, Windows에서 쓴 기록·inline 스크립트·Workspace는 계속 CRLF라 같은 코드의 해시와 Report manifest·게시본 해시가 플랫폼에 따라 달라진다. #13·#20에서 다시 결정해야 한다 |
| 지금대로 두고 한계로 확정 | 플랫폼마다 다른 해시와 git을 거친 거짓 `changed`가 새 기록과 게시본으로 이어진다. ADR-0003의 '기록은 그것을 만든 기계보다 오래 살아야 한다'와 어긋난다 |
| 줄 끝만 다르면 `same` | 고정 폭 파서·바이트 수·해시 대상 파일처럼 바이트에 민감한 입력의 실제 변경을 숨긴다. 기록한 해시의 뜻(실제로 읽은 바이트)과 화면의 판정이 어긋난다 |
| 줄 끝을 정규화한 해시를 함께 기록 | 텍스트 입력마다 두 번 해시하고 무엇이 텍스트인지 정해야 한다. 새 필드라 #13의 형식이 커지고 `/1` 기록에는 적용할 수 없다 |
| 바이트만 비교하고 사유를 붙이지 않음 | git을 거친 프로젝트에서 이유가 보이지 않는 `changed`가 이어지고 CONTEXT의 '+0 -0' 버전이 쌓인다 |
| 새 프로젝트에 `.gitattributes`를 넣지 않음 | 플랫폼을 오가는 인계(Q17-b)에서 git이 사용자 파일의 바이트를 바꿔 거짓 `changed`가 원천에서 생긴다 |

## 결과와 영향

- Windows 체크아웃·wheel·`kit_sha256`·기록·inline 스크립트·Report manifest·게시본의 해시가 플랫폼과 무관해진다.
  ADR-0006의 manifest와 #20 게시본의 해시가 결정적이 된다.
- 오래된 Windows 편집기에서는 LF인 로그·기록이 한 줄로 보일 수 있다.
- 쓰기 경로 여러 곳과 Windows CI 시험이 바뀐다.
- 새 프로젝트 안에서는 git이 줄 끝을 바꾸지 않는다. 원하지 않는 사용자는 그 파일을 지울 수 있다.
- git의 autocrlf로 오간 기존 프로젝트에는 'line endings only' 표시가 계속 보인다.
- `eol=crlf` 예외는 두지 않는다. 지금 저장소에는 Windows 전용 스크립트(`.bat`·`.cmd`·`.ps1`)가 없다. CRLF가 필요한
  파일이 생기면 예외 여부는 소유자가 정한다.
- 이 결정과 같은 PR에서 `tests/fixtures/records/`에 `* -text`를 먼저 두었다. 그 예제는 해시가 기대값이고 의도한 CRLF
  journal을 담는다. 루트 `.gitattributes`를 더할 때 이 규칙을 지우지 않는다.
- [현재 계약](../CONTRACTS.md) G절의 '줄 끝' 문장을 '결정됨, 구현 전 한계'로 바꿨다. 구현 PR이 그 문장을 지우고 F절에
  측정을 더하며, E절의 입력 상태 정의에 `line_endings_only`를 적는다.

## 관련

- Issue: [#37](https://github.com/handochan/aelix-analytics/issues/37), [#42](https://github.com/handochan/aelix-analytics/issues/42)(구현, 흐름 B),
  [#13](https://github.com/handochan/aelix-analytics/issues/13), [#10](https://github.com/handochan/aelix-analytics/issues/10),
  [#22](https://github.com/handochan/aelix-analytics/issues/22)(CI), [#23](https://github.com/handochan/aelix-analytics/issues/23)(Q17-b 인계),
  [#24](https://github.com/handochan/aelix-analytics/issues/24)(배포본 재현성).
- [ADR-0003](0003-portable-boundaries-and-in-process-kit-loading.md)(인코딩 규칙과 포트), [ADR-0006](0006-common-record-contract.md)
  10절(기록의 바이트 규칙), [현재 계약](../CONTRACTS.md) E·F·G절.
