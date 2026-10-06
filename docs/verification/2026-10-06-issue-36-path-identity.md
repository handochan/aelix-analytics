# Issue #36 경로 정체성 측정 — 2026-10-06

[#36](https://github.com/handochan/aelix-analytics/issues/36)의 구현(kit 0.5.0)은 기록이 담는 경로를 ADR-0006 9절의 경로 정체성으로
바꿉니다: 프로젝트 안이면 이름 공간의 프로젝트 상대 경로, 밖을 가리키는 링크는 경계, 철자는 저장된 이름. 기록 형식(경로 값)과 플랫폼
동작을 바꾸므로 운영 정책 5절의 측정 기록입니다. 이 기록은 **macOS 개발 기기**에서 잰 것입니다. Linux·Windows와 호스트 macOS는
PR의 CI(`pull_request` 실행과 `ci:macos` 라벨 실행)에서 채우며, 그 칸은 "CI 실행 예정"입니다 — 실행하지 않은 플랫폼은 통과가
아닙니다. 무엇이 계약에 어떻게 반영됐는지는 [CONTRACTS.md](../CONTRACTS.md) E(경로 정체성)·F1(kit 0.5.0)·G(경로 정체성의 한계)에
있습니다. 각 시험의 결과, JUnit 파일의 sha256, 시험이 남긴 관찰, M 규모 측정은
[2026-10-06-issue-36-path-identity.json](2026-10-06-issue-36-path-identity.json)에 있습니다.

## 출처와 환경

| 항목 | 근거 |
|---|---|
| 측정한 코드 | `feat/issue-36-path-identity`, 기준 `e9392d9`(PR #60·#61 병합 뒤의 main) 위의 작업 트리. 코드(`src`·`extension`·`tests`·`tools`·`pyproject.toml`·`uv.lock`, 152개 파일)는 이 기록을 더한 커밋의 코드와 같고, 이 기록과 문서만 측정 뒤에 썼습니다. 파일마다의 sha256 목록과 그 목록의 sha256이 JSON의 `measured_tree`에 있습니다 |
| 워크플로 | `.github/workflows/ci.yml`, 이 변경이 고치지 않음 |
| CI 실행 | CI 실행 예정: PR의 `pull_request` 실행(Ubuntu 24.04·Windows Server 2025)과 같은 헤드의 `ci:macos` 라벨 실행. 실행 번호·시도·이미지·인터프리터는 그 실행 뒤 이 기록에 적습니다 |
| 개발 기기 | macOS 26.6.2(25G83) arm64, uv 0.11.19. 저장소 시험 CPython 3.12.13(`uv.lock`의 개발 환경, DuckDB 1.5.5·Polars 1.44.2), aelix 레그 3.12.13·3.11.15, 런처·stdlib 스모크 3.11.15 |
| 격리 | `AELIX_ANALYTICS_TRUST_FILE`·`AELIX_ANALYTICS_UV`를 run의 evidence 폴더 안으로(`uv`는 실제 uv로의 링크), `LAB_*`·`AELIX_LAB_*` 없음, `~/.aelix`는 건드리지 않음 |
| 대소문자를 구별하는 볼륨 | 같은 기기에서 `hdiutil create -fs "Case-sensitive APFS"`로 만든 디스크 이미지를 pytest의 `--basetemp`로 써서, 경로 정체성 시험과 `tests/test_platform.py`의 링크·대소문자·재실행 시험을 한 번 더 돌렸습니다(대소문자를 구별하지만 유니코드 정규화는 무시하는 APFS) |

## 결과

| 검사 | Linux | Windows | macOS(호스트) | macOS(개발 기기) |
|---|---|---|---|---|
| 저장소 시험(`AELIX_ANALYTICS_TEST_INSTALL=1`, 배포본 end-to-end 포함) | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | 1,002 passed, 37 skipped, 0 failed(280 s) |
| 대소문자를 구별하는 APFS 볼륨: 경로 정체성·링크·대소문자·재실행 시험 | — | — | — | 238 passed, 6 skipped |
| aelix 0.1.0b2 통합(`tests/test_extension.py`·`tests/test_usage_report.py`), CPython 3.12 / 3.11 | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | 143 / 143 passed |
| 런처, CPython 3.11(`tests/test_launcher.py`) | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | 120 passed, 1 skipped |
| 플랫폼 스모크, required gate | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | held: 25 hold |
| stdlib 스모크, CPython 3.11 | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | held: 15 hold |
| 사용량 보고서(`tools/usage_report.py`) | CI 실행 예정 | CI 실행 예정 | CI 실행 예정 | 필수 사실 58/58, 생략 불변식 42/42 |
| 네이티브 콘솔, LongPathsEnabled=0의 긴 경로 | — | CI 실행 예정 | — | — |

- 개발 기기의 건너뜀은 Windows 전용 시험(콘솔 21, 긴 경로 6, `taskkill`, 대소문자 구별 디렉터리 2, 8.3 짧은 이름), aelix가 없는 환경의 두
  모듈(aelix 레그가 돌림), APFS가 거절하는 UTF-8이 아닌 이름 2, CPython 3.14에만 있는 잠금 사례, POSIX에서 다시 보내는 런처 사례입니다.
  대소문자를 구별하는 볼륨에서는 `Runs`로 저장된 `runs/`의 재실행 시험(대소문자를 무시하는 곳의 시험)이 더 건너뜁니다.
- 사용량 보고서의 크기는 `e9392d9`에서 같은 명령으로 잰 것과 같습니다. 다른 세 값(호스트의 기본 프롬프트 크기)은 체크아웃의
  경로를 두 번 담는데, 기준을 62자 긴 경로에서 돌렸기 때문입니다(124자 차이).
- JUnit(sha256 앞 12자, 전체는 JSON): 저장소 시험 `d4a87752cc0a`, 대소문자 구별 볼륨 `a9884d0fadc4`, aelix 3.12 `5a900f14dff5`,
  3.11 `c620f24c3ea1`, 런처 `0340b653f164`.

## 항목별 전후

"이전"은 [2026-10-02 인벤토리](2026-10-02-issue-8-windows-inventory.md)(`4eb0955`)의 같은 이름의 관찰입니다(JSON의 `before.pointers`).
"이후"의 macOS 칸은 개발 기기의 관찰이고, 다른 칸은 CI 실행 예정입니다.

| # | 사례(관찰) | 이전 | 이후, macOS 개발 기기 | Linux | Windows |
|---|---|---|---|---|---|
| 1 | 심볼릭 링크인 `data/`(`linked-data-symlink`) | 세 OS 모두: 저장 run `outputs: []`, 입력은 링크를 푼 절대 경로(`external: true`), 데이터셋은 절대 경로이고 producer 없음 | 저장 run의 출력 `data/derived/part.parquet`(`created`, `via: lab.save`), 입력 `data/raw/sales.parquet`·`external: false`, 경고 없음. 읽기 run의 입력 `data/derived/part.parquet`. 데이터셋은 상대 경로이고 `part`의 producer = 저장 run, `used_by` = 읽기 run | CI 실행 예정 | CI 실행 예정 |
| 1 | 질의(`query-linked-data-symlink`) | — | 링크한 `data/`의 view를 읽음(120행). 밖의 파일, `data/` 대상 안의 링크로 밖, `data/../lab.json`은 `Permission Error`. `data/` 안에서 밖을 가리키는 링크 아래의 데이터셋은 view에 있고 질의가 거절되며 hint가 실제 위치 판정을 말함 | CI 실행 예정 | CI 실행 예정 |
| 2 | junction인 `data/`(`linked-data-junction`, `query-linked-data-junction`) | Windows: 출력은 `data/derived/part.parquet`(diff), 입력·데이터셋은 절대 경로라 producer가 이어지지 않음 | — | — | CI 실행 예정 |
| 3 | `runs/`를 가리키는 링크(`link-into-runs-*`) | Windows junction: run이 `lnk/<id>/stdout.txt`·`stderr.txt`를 출력으로 기록하고 경고 셋. 심볼릭 링크: 기록 안 함 | 심볼릭 링크: `outputs: []`, 경고 없음(시험이 단언) | CI 실행 예정 | CI 실행 예정(junction이 바뀜) |
| 4 | 링크인 기록 파일(`trust-link`, `record-link-replaced`, `append-link-followed`) | macOS 관찰: `write_json`이 링크를 보통 파일로 바꿈(신뢰 목록 포함) | 신뢰 목록: 링크는 링크로 남고 대상에 새 항목, 끊긴 링크는 파일로 바뀜. 다른 기록의 교체는 링크를 파일로 바꿈(의도한 규칙), append는 링크 대상에 씀 | CI 실행 예정 | CI 실행 예정 |
| 5 | 대소문자 별칭(`case-aliases`) | macOS 조사 case-4: 다른 대소문자의 스크립트가 두 번째 차트 정체성, 다른 대소문자의 절대 경로가 `script_outside`, 다른 대소문자로 읽은 입력은 producer와 끊김, 신뢰 목록이 맞지 않음 | 대소문자 무시(기본 APFS): `Scripts/Analysis.py`의 run이 `scripts/analysis.py`로 기록되어 차트 버전 둘이 한 정체성, 다른 대소문자의 절대 경로 받음, `AGG.parquet`으로 읽은 입력이 `agg`의 producer와 이어짐, 다른 대소문자의 부모로 만든 프로젝트 신뢰. 대소문자 구별 볼륨: 다른 철자는 `script_missing`·`script_outside`, 읽기는 실패(파일이 없음) | CI 실행 예정 | CI 실행 예정 |
| 5 | NFD 별칭(`nfd-aliases`) | 추론(열기만 관찰) | NFC 이름이 NFD 파일을 열고 기록은 저장된 NFD(기본 APFS와 대소문자 구별 APFS 모두) | CI 실행 예정 | CI 실행 예정 |
| 6 | 대소문자만 다른 데이터셋(`case-only-datasets`) | Linux(순수 함수): 사람의 `Sales.parquet`이 `sales`를 먼저 받음 | 기본 APFS: 둘이 함께 있을 수 없음(`FileExistsError`, 같은 파일). 대소문자 구별 볼륨: 둘이 함께 있고 kit의 것이 `sales`, 사람의 것이 `sales_2`, `status`가 경고 | CI 실행 예정 | CI 실행 예정(대소문자 구별 디렉터리판 `case-only-datasets-case-sensitive-dir` 포함) |
| — | 규칙 공통(`two-links-one-target-symlink`, `file-link-out`, `ancestor-link-symlink`, `inward-data-link`) | — | 같은 외부 디렉터리의 두 링크: 쓴 run과 읽은 run이 `data/a/t.parquet` 하나로 이어짐(producer 확인). 밖으로 가는 파일 링크: 링크의 경로·`external: false`. 조상 링크 너머는 절대 경로, 돌아오면 상대 경로, 경계 뒤의 `..`는 POSIX에서 대상의 부모(연 파일과 같은 정체성). 안을 가리키는 `data` 링크: 데이터셋·입력이 실제 위치(이전과 같음) | CI 실행 예정 | CI 실행 예정(junction 포함) |

## M 규모(300 run, 1만 데이터 파일)

같은 기기, 같은 fixture에서 기준 `e9392d9`의 `src`와 측정한 트리의 `src`를 번갈아 잰 값입니다(최솟값, run의 evidence에 있는
스크립트). 시간은 공유 러너에서 단언하지 않으며 시험은 관찰(`walk-m-scale`)만 남깁니다.

| 측정 | `e9392d9` | 헤드 |
|---|---|---|
| 트리 조회(`runner.tree_stat`), 보통 `data/` | 44.5 ms, 파일 10,006 | 22.8 ms, 파일 10,006 |
| 트리 조회, 심볼릭 링크인 `data/` | 0.1 ms, `data/` 아래 0개(기록 손실) | 23.7 ms, `data/` 아래 10,000개 |
| 데이터셋 목록, 보통 / 링크 | 398.3 ms / 552.1 ms(보통은 1만 개 모두 상대 경로, 링크는 모두 절대 경로) | 69.6 ms / 97.9 ms(모두 상대 경로) |
| 경로 1만 개의 정체성(`Project.rel`), 보통 / 링크 | 345.4 ms / 492.2 ms(보통은 1만 개 모두 상대 경로, 링크는 모두 절대 경로) | 65.3 ms / 91.2 ms(모두 상대 경로) |
| `core.status`(aelix 프로세스가 만드는 개요), 보통 / 링크 | 509.3 ms / 679.8 ms | 219.0 ms / 258.2 ms |

시험의 관찰(`walk-m-scale`, 개발 기기): 이전 방식의 트리 조회 44.2 ms 대 새 조회 23.2 ms(같은 파일 집합을 단언), 목록을 읽은 뒤 만든
1,000개의 정체성 7.9 ms, 1,000개를 하나씩 만들고 바로 정체성을 정한 것 135.6 ms(디렉터리를 파일마다 다시 읽지 않음: 처음 여덟 번
뒤로는 점점 드물게 다시 읽습니다. 파일마다 읽으면 3,000개에서 1.9 s, 1만 개에서 수십 초였습니다).

## 계약에 반영한 것

- E: "경로 정체성"·"데이터셋 목록" 행을 새로 두고, "입력 기록"·"view 이름" 행을 고쳤습니다. B에 출력 비교 대상(이름 공간)과
  "기록의 경로", C2·C4에 `path`·`external`·`outside_data`·`source.path`의 규칙, D에 `status`의 `case_only_datasets`를 적었습니다.
- F: 스크립트 경로의 포함 판정(철자 맞춘 실제 경로), 배포 버전 0.5.0, 이 기록의 링크. F1: kit 0.5.0에서 바뀐 것.
- G: "프로젝트 안의 디렉터리 링크"와 "대소문자와 유니코드 정규화" 항목을 지우고 "경로 정체성의 한계"로 바꿨습니다. 실행하지 않은
  것에서 "Linux·Windows에서 심볼릭 링크인 기록 파일을 고쳐 쓰는 것"과 "프로젝트 안에서 조상 디렉터리를 가리키는 junction"을 뺐습니다
  (이 PR의 시험이 세 OS에서 돌림 — Linux·Windows의 결과는 CI 실행 예정).

## 실행하지 않은 것

- Linux·Windows·호스트 macOS의 모든 레그: CI 실행 예정(위 표). 그 전까지 그 플랫폼의 결과는 `not_run`입니다.
- Linux의 casefold 디렉터리(ext4 `+F`), HFS+, 네트워크·클라우드 볼륨, `st_ino`가 0인 파일 시스템(경로로 판정하는 대비책은 단위
  시험만), bind mount 별칭(권한이 필요; 파일 키로 판정하는 규칙은 조회를 거치는 시험만).
- Windows의 클라이언트 판·표준 사용자 토큰·`mklink`·`New-Item`으로 만든 링크(2026-10-02 기록과 같음).
- 8.3 짧은 이름이 꺼진 Windows 볼륨에서는 짧은 이름 시험이 건너뜁니다(CI 실행에서 어느 쪽인지 적습니다).
