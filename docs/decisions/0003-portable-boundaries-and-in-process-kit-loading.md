# ADR-0003: 이식 가능한 경계와 aelix 프로세스의 kit 로딩

Date: 2026-09-22

Status: Accepted (2026-09-22) — [ADR-0002](0002-python-support-and-platform-scope.md) 결정 5의 구현 방식을
정하는 결정이며 [Issue #8](https://github.com/handochan/aelix-analytics/issues/8)의 첫 PR에서 적용했다.
새 제품 결정이 아니라 승인된 작업 안의 구조 선택이므로, 반대 의견은 해당 PR 리뷰에서 다룬다.
현재 코드가 실제로 하는 것은 [현재 계약](../CONTRACTS.md)의 "실행 환경과 지원 플랫폼" 절에 있다.

개정: 2026-10-06 — [#9](https://github.com/handochan/aelix-analytics/issues/9)의 첫 PR이 kit을 package `aelix_analytics`로
옮기면서 결정 2·3의 경로와 이름을 고쳤다([ADR-0005](0005-names-first-storage-with-the-new-writer.md) 결정 7). 결정의 내용(포트의
위치가 한 파일, 합성 package로 `core`·`_platform`만 로드)은 그대로다. 배경과 대안의 경로는 당시의 것이다.
#9의 둘째 PR(kit 0.3.0)이 결정 3의 합성 package 이름과 변수 이름을 새 공개 이름에 맞춰 고쳤다. 내용은 그대로다.

## 배경

ADR-0002 결정 5는 "파일 잠금·실행 타임아웃·자식 프로세스 생성과 종료·파일 교체·파일명·경로 표현·텍스트 인코딩을
처음부터 이식 가능한 내부 경계로 정의하고 플랫폼별 구현을 주입한다. 호출부에 `if sys.platform` 분기를 흩지 않는다"를
확정했다. 그 경계를 **어디에** 두는가가 결정되지 않았고, 한 가지 제약이 선택지를 좁혔다.

확장은 `kit/labkit/core.py`를 aelix 프로세스 안에서 `spec_from_file_location`으로 **최상위 모듈**로 로드했다.
최상위 모듈에는 package가 없으므로 `core.py`는 상대 import를 쓸 수 없었다. 그런데 잠금은 aelix 프로세스 안에서 실행된다
(`/lab note`·`/lab gap`의 `core.add_note`와 `on_before_agent_start`의 `core.sync_context`가 `project_lock`을 쓴다).
기록 교체(`core.write_json`)와 생존 확인(`list_runs`)도 같다. 즉 경계는 그 로드에서 닿을 수 있어야 했다.

## 결정

1. **포트는 네 개다.** 플랫폼마다 **동작이 다른** 것만 포트로 만든다: `FileLocks`, `AtomicFiles`,
   `ProcessControl`, `RunControl`. 나머지(예약 파일명, `file://` URL, 텍스트 인코딩)는 세 플랫폼에서 같은 규칙이
   맞으므로 포트로 만들지 않는다. 포트를 일곱 개로 나누지 않는다.
2. **포트는 `_platform.py` 한 곳에 둔다**(#9-1부터 `src/aelix_analytics/_platform.py`, 그 전에는 `kit/labkit/_platform.py`).
   stdlib만 쓰고 kit의 다른 모듈을 import하지 않는다.
   `core.py`가 커져 프로젝트 모델과 OS 계층을 겸하는 것을 피하고, 실행 제한 시간과 자식 프로세스 제어처럼
   프로젝트 모델이 아닌 책임을 같은 파일에 넣지 않는다.
3. **확장은 kit을 합성 package의 submodule로 로드한다.** package 이름은 `aelix_analytics_runtime_<kit 경로 해시>`(kit 0.3.0 전
   `aelix_lab_kit_<…>`)이고
   `__path__`는 그 pack의 실행 루트 안의 package 디렉터리다(#9-1부터 체크아웃의 `src/aelix_analytics`, 설치본의
   `aelix_analytics/kit/aelix_analytics`. 그 전에는 `kit/labkit`). `core.py`는 평범한 상대 import를 쓴다. `sys.modules` 항목은
   실행 중에만 존재하고 끝나면 지운다. 따라서 호스트 환경에 설치된 같은 이름의 package(#9-1부터는 배포본 자신의
   `aelix_analytics`, 그 전에는 `labkit`)를 가리지 않고, 두 pack이나 `AELIX_ANALYTICS_RUNTIME_PATH`(옛 `AELIX_LAB_KIT`) 변경
   전후가 캐시된 형제 모듈을 공유하지 않으며, 실패한 로드는 아무것도 남기지 않는다.
4. **구현은 `Project`가 나른다.** `core.PLATFORM`은 모듈을 불러올 때 `_platform.detect()`가 한 번 고른 이 프로세스의
   구현이고 `Project(root)`의 기본값이다. 진입점은 번들을 따로 넘기지 않고, 시험과 다른 호출자는
   `Project(root, platform=…)`로 다른 구현을 넣는다. 환경 변수나 기록 속 문자열로 구현을 고르지 않는다.
5. **run 제어의 계약은 하나다.** `RunControl.guard()`가 run 전체를 한 번 감싸 중단 시그널의 처리기를 설치하고, 기록을
   쓴 뒤 되돌린다. 결과는 가드의 두 표시 `cancelled`·`expired`이며, 각 처리기가 다른 무엇을 부르기 전에 첫 문장에서
   대입으로 세운다. 호출부는 예외 종류가 아니라 이 표시로 상태를 판정한다. 주 스레드를 중단하는 것은 가드이고,
   `interruptible()` 구간 안에서 **최대 한 번**, 항상 `_platform.Interrupted`로 한다. Windows에서는 요청 파일을 보는 감시
   스레드나 제한 시간 타이머가 `signal.raise_signal`로 올린 SIGINT를 통해서다.
6. **새 이름만 거절한다.** Windows 예약 장치명은 사용자가 새 이름을 정하는 지점에서만 거절한다:
   `lab_import --name`과 `lab_run`이 만드는 `scripts/<name>.py`다. run 안의 `lab.save`는 거절하지 않고 run의
   `warnings`에 한계를 남긴다 — 보존된 스크립트는 출력 파일이 지워진 뒤에도 다시 실행돼야 하고, 거절하면
   과거 분석의 재실행이 막힌다. 차트는 정체성(`script::id`)을 생성된 id까지 그대로 두고 저장 파일명만 바꾸며,
   그 대응은 단사여야 한다. 기록은 그것을 만든 기계보다 오래 살아야 한다.

## 대안과 선택 이유

| 대안 | 검토 결과 |
|---|---|
| 경계를 `core.py` 안에 둔다 | 로더를 바꾸지 않아도 되지만 1,000줄짜리 프로젝트 모델이 OS 계층을 겸하게 되고, 실행 제한 시간·자식 프로세스 제어가 프로젝트 모델에 들어간다 |
| 확장이 `sys.path`에 kit을 넣고 `import labkit.core` | 독립 리뷰에서 분석 라이브러리를 끌어오지 **않음**이 확인됐다(`capture`의 무거운 import가 전부 지연된다). 그래도 호스트 환경에 설치된 `labkit`과 서로를 가린다. 경로 기반 로드는 그 충돌을 피하려고 존재한다 |
| `core.py`가 형제를 이중 모드로 import(상대 import 실패 시 경로 로드) | 여섯 줄이면 되지만 두 경로를 모두 시험해야 하고, 리뷰어가 매번 정당성을 따져야 하는 우회로가 남는다 |
| 일곱 개 관심사를 일곱 개 포트로 | 동작이 플랫폼마다 다르지 않은 규칙까지 주입 지점을 만든다. 뒤에 아무것도 없는 seam이 늘어난다 |
| 예약 이름을 모든 시점에 거절 | 동작 보존이 아니다. `con`·`nul`은 지금 dataset 이름 검사를 통과하고, `lab.save(df, "con")`을 담은 보존 스크립트가 POSIX에서도 재실행되지 않게 된다 |
| 예약 이름 거절의 기준을 "파일이 이미 있는가"로 | 독립 리뷰가 반례를 재현했다. 파생 파일을 지운 뒤 보존된 run을 재실행하면 거절된다. 기록이 아니라 파일 시스템 상태를 호환성 기준으로 삼은 것이 잘못이었다 |
| 차트 파일명에 `-file` 같은 접미사 | 단사가 아니다. `con-file`은 평범한 id라서 두 차트가 같은 파일을 덮어쓰고, 재실행 비교가 서로 다른 차트를 `match`로 보고한다. 독립 리뷰가 재현했다. slug에 들어갈 수 없는 `_`를 쓴다 |
| 콜백이 직접 예외를 올린다 | 두 구현이 같은 계약을 지킬 수 없다. Windows에서는 콜백이 타이머 스레드에서 돌아 예외가 그 스레드를 끝낸다 |
| 처리기가 한 번을 정한 뒤 콜백으로 결과를 알린다 | 스크립트가 남긴, 예외를 올리는 처리기가 정하기와 알리기 사이에서 돌면 취소가 기록에서 빠진다. 독립 리뷰가 재현했다(실제 시그널 100회 중 40–54회). 첫 문장의 대입으로 바꾼 뒤 그 창은 처리기의 첫 명령 하나로 줄었고(그때 8–15회), 그 지점에서 끊긴 취소는 끊은 예외의 traceback에서 센다. 지금 구현을 실제 시그널로 시험한 시도는 모두 `cancelled`였다([측정 기록](../verification/2026-09-24-issue-8-platforms.md)) |
| Windows에서 `_thread.interrupt_main`으로 중단 | 측정에서 잠든 주 스레드를 깨우지 못했다. `signal.raise_signal`은 C 처리기를 호출한 스레드에서 돌려 그 이벤트를 세우므로 `time.sleep`이 돌아온다 |
| Windows에서 `CTRL_BREAK_EVENT`로 묻기 | `uv run` 그룹에 보내면 `uv`가 곧바로 끝나고 그 자식은 기록 없이 남아 `taskkill /T`도 닿지 않는다(측정). 요청 파일은 `uv`를 거쳐, `uv`가 끝난 뒤에도 kit에 닿는다 |
| POSIX에서도 요청 파일로 묻기 | GIL을 쥔 C 코드 안의 주 스레드에는 Python 스레드가 닿지 못한다(재현: 파국적 정규식에서 SIGINT 0.08초, 요청 파일 18.9초). POSIX는 GIL 없이 도는 C 수준 시그널 처리기가 있으므로 SIGINT로 묻는다 |
| 시그널을 `signal.set_wakeup_fd`로도 기록 | 첫 명령의 창까지 닫을 수 있지만 프로세스 전체 자원이라 asyncio 루프가 쓰는 것을 대체한다. 첫 명령에서 끊긴 취소는 그 처리기를 끊은 예외의 traceback에 처리기의 frame이 남으므로, run 안에서 그것으로 센다 |

## 결과와 비용

- POSIX 전용 호출은 `_platform.py` 한 파일에만 남는다. ADR-0002가 나열한 일곱 관심사의 호출부에는 `sys.platform` 분기가 없다.
  남은 예외는 `/lab open`의 브라우저 실행(`extension/aelix_lab.py`)이며, 사람용 진입점을 다루는 다음 PR의 범위다.
  (그 PR에서 `ProcessControl.open_document`로 옮겼다 — [ADR-0004](0004-minimal-distribution-and-entry-points.md).)
- 확장의 로더가 바뀌었으므로 [현재 계약](../CONTRACTS.md) A절의 "무엇을 import하는가"가 함께 바뀐다.
- Windows 구현은 GitHub Actions의 Windows Server 2025에서 이 코드로 측정했다([측정 기록](../verification/2026-09-24-issue-8-platforms.md)).
  확인한 것, 선언한 한계, 실행하지 않은 것은 [현재 계약](../CONTRACTS.md) G절 한 곳에 두고 구현의 주석은 그곳을 가리킨다.
  구현의 존재를 동작 증거로 쓰지 않는다.
- Windows의 취소는 POSIX보다 약하다. SIGTERM을 가로챌 수 없으므로 강제 종료는 `cancelled`가 아니라
  `incomplete`로 남고, GIL을 쥔 C 코드와 SIGINT 이벤트를 보지 않는 대기에는 취소가 닿지 않는다(측정). 제한 시간도
  같은 이유로 닿지 않는다고 본다(추론, 현재 계약 G절).
- 한 번만 중단하는 규칙에는 비용이 있다. `BaseException`을 잡는 스크립트와 finalizer 안에서 처리된 시그널은 그 한 번을
  써 버리고, 스크립트가 남긴 처리기에는 좁은 창이 남는다. 모두 현재 계약 G절에 적는다.
- POSIX 동작도 일부 바뀐다(현재 계약 F1). 새 이름과 경로의 거절, 중단의 분류, 인코딩이다. 이 PR의 리뷰에서 받아들이거나
  거절한다.
- 강제 종료가 실패해도 세션을 붙잡지 않는다. `taskkill`이 실패하면 그 사실을 stderr에 남기고 대기는 제한 시간으로
  끊는다. 그 경우 자식 프로세스가 살아남으며, 이를 정리 성공으로 적지 않는다.
- 대체 구현을 주입한 계약 시험이 생겼다. 기록이 같다는 것만으로는 교체 가능성을 증명하지 못하므로,
  시험은 실제 구현이 호출되면 실패하도록 무장한 상태에서 수행한다.

## 구현에서 확인할 것

1. 두 번 로드해도 서로 다른 `core`와 `_platform`을 얻고 `sys.modules`에 남는 것이 없다. (PR-1에서 확인)
2. 확장 로드가 분석 라이브러리를 aelix 프로세스로 들이지 않는다. (PR-1에서 확인)
3. 주입한 대체 구현이 잠금·기록 교체·run 제어·생존 확인에 쓰이고, 실제 구현은 호출되면 실패하도록 무장한 상태에서
   호출되지 않는다. (PR-1의 시험) 확장의 자식 프로세스 제어와 CLI의 요청 파일 감시는 이 시험 밖이며, 실행으로 확인했다.
4. 제한 시간·취소의 분류(D1). 블로킹 DuckDB 호출과 `time.sleep`의 제한 시간, 준비 구간과 스크립트 구간의 취소, 기록
   단계의 취소를 PR-1의 시험과 측정 도구로 확인했다. aelix 중단 경로는 aelix 0.1.0b2에서 `tests/test_extension.py`로
   확인했다: macOS·Linux·Windows에서 각각 12 passed([측정 기록](../verification/2026-09-24-issue-8-platforms.md)).
5. 잠금의 상호 배제, 자식 종료, 제한 시간을 Linux·macOS·Windows에서 측정 도구로 실행했다(PR-1, [측정 기록](../verification/2026-09-24-issue-8-platforms.md)).
   저장소 CI(`.github/workflows/ci.yml`, Issue #8 PR-3a)가 같은 측정 도구와 저장소 시험을 Linux·Windows에서는 모든 PR과
   main push마다, macOS에서는 `ci:macos` 라벨·수동 실행·주간 실행에서 돌린다(소유자 결정, 2026-10-02).
6. 예약 이름은 Windows Server 2025의 파일 시스템에서 확인했다(`con.txt`·`nul.parquet`는 보통 파일로 저장되고 확장자 없는
   `nul`은 여전히 장치다). 콘솔: 설치본 `analytics`의 run에 Ctrl+C와 Ctrl+Break를 보내 측정했다 — 의사 콘솔(ConPTY)에서는
   입력 `\x03`(Ctrl+C)과 도우미 프로세스의 `GenerateConsoleCtrlEvent`·win32-input-mode 입력(Ctrl+Break)으로, 고전 콘솔에서는
   도우미로(Ctrl+C는 `cancelled` 기록 뒤 3, Ctrl+Break는 uv가 먼저 끝나고 kit이 나중에 `cancelled`). Python의 콘솔 경로로
   쓴 kit의 출력(결과 줄의 한국어 제목)은 콘솔 코드 페이지 437과 949에서 바르게 보였다(런처의 글은 같은 경로이나 실행하지
   않았다). 사람이 보이는 창에서 누른 키와 콘솔 파일 기술자에 직접 쓴 바이트는 `not_run`이다([측정 기록](../verification/2026-10-02-issue-8-windows-inventory.md), CONTRACTS G).
