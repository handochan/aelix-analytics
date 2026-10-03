# ADR-0009: 첫 게시 대상 — 사용자가 지정한 폴더

Date: 2026-10-03

Status: Accepted — 2026-10-03 소유자 결정 (Issue #21)

[Issue #21](https://github.com/handochan/aelix-analytics/issues/21)의 완료 조건 1 "구현 전에 첫 대상·접근 범위·실패/재시도 계약을
기록한다"를 채우는 결정이다. 소유자가 조사의 권장안을 채택했다: 첫 P1 대상은 사용자가 지정한 폴더(로컬 폴더, 마운트한 공유,
동기화 폴더)이고, 접근 범위는 그 폴더의 권한이며 "팀 전용"으로 표시하지 않고, 확인을 마친 버전만 최신 주소가 된다.
채택은 구현이 아니다. `build`·`publish`는 현재 코드에 없고([현재 계약](../CONTRACTS.md)), 구현은
[#20](https://github.com/handochan/aelix-analytics/issues/20)의 독립 build 뒤에 #21에서 한다. #21은 그 구현을 위해 열려 있다.
receipt의 기록 형식은 [#4](https://github.com/handochan/aelix-analytics/issues/4)의 공통 기록 계약(ADR-0006)을, kit이 쓰는 텍스트의
줄 끝은 [#37](https://github.com/handochan/aelix-analytics/issues/37)의 결정(ADR-0007)을 따른다. 명령의 인자 문법과 receipt 필드의
철자는 제안이며 구현 PR이 정한다.

## 배경

P1은 하나의 검증된 대상부터 시작한다([공유 설계](../PUBLISH-AND-SHARING.md) §6·§8). [베타 기준](../BETA-QUALITY-GATES.md)의 Q14는
그 대상에서 다른 독자의 접근, 페이지와 자원의 일치, 고정 버전 주소와 최신 주소, 중단과 재시도, 이전 게시본 유지를 요구한다.
"팀 전용"은 대상이 실제 권한 기능을 가질 때만 말할 수 있다(공유 설계 §6 팀 접근). [아키텍처](../ARCHITECTURE.md) §5는 `Publisher`의 첫
구현 후보를 공유 폴더로 적었다.

이 결정을 좁힌 사실은 다음과 같다.

- 지금의 `report/`는 프로젝트 파일을 `../` 상대 경로와 `file://` 절대 URL로 가리킨다(`kit/labkit/report.py`의 `Site.file_link`).
  그 자체로는 공유본이 아니며, 독립 패키지는 #20이 만든다.
- 소유자 팀의 실제 환경(웹 호스팅, SSO, 공유 드라이브)을 모른다. 새 자격 증명 없이 CI의 세 OS와 소유자 기기에서 끝까지 시험할 수
  있는 대상이 필요하다.
- 배포본 wheel은 의존성이 없다(현재 계약 F).
- 이 저장소는 private이고 Pages가 꺼져 있으며(`gh api repos/handochan/aelix-analytics`, 2026-10-03: `private: true`,
  `has_pages: false`), 요금제는 GitHub Free다([기여 가이드](../../CONTRIBUTING.md)의 저장소 CI). GitHub 문서(2026-10-03 확인)에 따르면
  GitHub Free의 Pages는 공개 저장소에서만 쓸 수 있고
  ([About GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/about-github-pages)), Pages 사이트는
  저장소가 private이어도 인터넷에 공개되며([Creating a GitHub Pages site](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)),
  비공개 게시는 GitHub Enterprise Cloud의 조직에서만 된다([Changing the visibility](https://docs.github.com/en/pages/getting-started-with-github-pages/changing-the-visibility-of-your-github-pages-site)).
- 사람 검증은 소유자가 직접 한다(2026-10-03 소유자 결정, [#39](https://github.com/handochan/aelix-analytics/issues/39)).

## 결정

1. **첫 대상은 사용자가 지정한 폴더 하나다.** 로컬 폴더, 마운트한 네트워크 공유(SMB·NFS), 동기화 폴더(Dropbox·OneDrive 등)를 모두
   같은 폴더로 다룬다. 새 자격 증명·서버·의존성이 없다. 웹 주소와 접근 제어는 그 폴더와 폴더를 내보내는 인프라의 몫이다.
   대상은 프로젝트 루트 밖이어야 한다. 프로젝트 안의 파일은 run의 출력 비교 대상이라(현재 계약 B) 게시가 동시에 도는 run의 출력으로
   기록될 수 있다. 다른 provider는 같은 `Publisher` 포트와 receipt 계약의 adapter로 더한다(P2).
2. **명령은 `analytics publish`다.** 입력은 #20이 만든 빌드의 `publication_id`와 대상(폴더 경로 또는 저장한 대상 이름)이다.
   그 밖의 동작은 최신 주소를 그 channel의 이전 버전으로 되돌리는 것 하나다. 인자 문법은 구현 PR이 정한다(공유 설계 §1).
   제안: `analytics publish --build <publication_id> --to <폴더>`, 저장한 대상은 `--target <이름>`, 되돌리기는
   `--switch-to <publication_id>`. kit의 하위 명령이므로 설치본의 `analytics` 런처와 체크아웃 래퍼가 같은 계약으로 얻는다.
   상시 `analytics_publish` 모델 도구는 두지 않는다. 에이전트는 사용자가 명시적으로 게시를 요청할 때 같은 CLI와 `/analytics`
   경로를 쓴다. 자동 게시는 P1에 넣지 않는다.
3. **저장한 대상은 git이 무시하는 로컬 설정에 둔다.** 폴더 경로는 기기마다 달라 프로젝트 기록에 넣지 않는다. 위치와 파일 이름은
   #13의 관리 디렉터리를 따라 구현 PR이 정하며, 새 프로젝트와 전환한 프로젝트 모두에서 git이 무시하는 곳이어야 한다.
   그때 쓴 경로는 receipt(결정 7)에 남는다.
4. **대상의 배치는 다음과 같다.**

   ```text
   <대상>/
     publications/<publication_id>/   버전. 패키지 바이트 그대로이며 한 번 쓰면 바꾸지 않는다
       index.html                     버전 주소
     projects/<channel>/
       latest.json                    최신 포인터의 정본
       index.html                     최신 주소. latest.json으로 만든 페이지
     .staging/                        전송 중인 사본과 잠금 파일
   ```

   - 게시는 버전 디렉터리에 파일을 더하거나 고치지 않는다. 게시 시각·대상 같은 전달 정보는 receipt와 포인터에만 둔다
     (공유 설계 §4: 전달 메타데이터는 계산 결과의 해시와 분리).
   - `latest.json`은 최신 `publication_id`, 버전 주소(대상 안의 상대 경로), 그 channel의 이전 버전 목록을 담고 형식 버전을 가진다.
   - `projects/<channel>/index.html`은 최신 버전으로 보내는 meta refresh, JavaScript 없이 누를 수 있는 링크, 버전 목록, 접근 범위
     문구(결정 8)를 담는다. 링크는 `../../publications/<id>/index.html`처럼 대상 안의 상대 경로이고 `index.html`을 직접 가리킨다.
     `file://`에서는 디렉터리 주소가 `index.html`로 열리지 않는다.
   - channel의 기본값은 프로젝트 제목의 slug다(비면 정해 둔 기본 이름). 대상 설정에서 바꿀 수 있다. 사용자가 새 이름을 정하는
     지점이므로 모든 OS에서 쓸 수 없는 이름은 거절한다([ADR-0003](0003-portable-boundaries-and-in-process-kit-loading.md) 결정 6).
   - 심볼릭 링크·junction을 만들지 않는다. Windows·SMB·동기화 폴더에서 만들 수 없거나 따라가지 않는다.
   - `latest.json`과 `projects/<channel>/index.html`은 kit이 쓰는 텍스트이므로 모든 OS에서 UTF-8·LF로 쓴다(#37, ADR-0007).
5. **게시는 네 단계이고, 최신 주소는 확인을 마친 버전만 가리킨다.**

   | 단계 | 하는 일 | 실패하거나 중단되면 |
   |---|---|---|
   | 전송 | 패키지를 `.staging/` 아래의 새 디렉터리에 복사한다 | 최신 주소는 그대로다. 남은 staging은 다음 게시가 잠금 안에서 지운다 |
   | 확인 | 대상에서 다시 읽어 manifest와 manifest가 적은 모든 파일의 sha256을 빌드와 대조하고, entry `index.html`부터 패키지 안의 모든 `href`·`src`가 패키지 안에서 풀리는지 확인한다 | 전환하지 않는다. 맞지 않은 파일·링크를 receipt에 적는다 |
   | 확정 | staging 디렉터리를 아직 없는 이름 `publications/<publication_id>/`로 바꾼다. 같은 볼륨 안의 이름 변경이다 | 이미 있는 이름은 바꾸지 않는다(결정 6). 그 밖의 실패에서도 전환하지 않는다 |
   | 전환 | `latest.json`, 그다음 `projects/<channel>/index.html`을 각각 원자적으로 교체한다 | 이전 포인터가 남는다. 다음 게시나 재시도가 전환만 다시 한다 |

   - 전송·확인·확정은 "파일 전달" 하나의 재시도 단위이고, 전환은 별도 단위다(공유 설계 §6).
   - 두 포인터를 함께 묶는 트랜잭션은 없다. 전환 도중에 멈추면 둘이 서로 다른 버전을 가리킬 수 있으나 둘 다 확인한 버전이다.
     receipt는 이를 전환 미완료로 적고, 다음 전환이 맞춘다.
   - 포인터 교체는 기존 `AtomicFiles.replace`(Windows의 교체 재시도 포함)를 쓴다. 새 포트를 만들지 않는다(ADR-0003 결정 1).
   - Ctrl+C 같은 중단을 받으면 다음 단계를 시작하지 않는다. 전환 도중에 받으면 위의 전환 미완료와 같다. Windows에서 경로가 길어
     쓰지 못하면 전송 실패로 끝난다.
   - 같은 대상의 게시는 대상 안의 잠금 파일을 `FileLocks`로 잡아 직렬화한다. 네트워크 파일 시스템의 잠금은 최선 노력이고,
     같은 대상에 동시에 게시하는 것은 지원하지 않는다. 잠금 없이 겹쳐도 확정은 없는 이름으로만 하므로 버전을 덮어쓰지 않고,
     최신 주소는 확인한 버전만 가리킨다. 어느 쪽이 마지막 최신이 되는지는 보장하지 않는다.
6. **재시도의 단위는 `publication_id`, 패키지 해시, 같은 대상이다**(공유 설계 §6). 패키지 해시는 manifest 파일의 sha256이다.
   manifest가 모든 파일의 sha256을 담으므로 패키지 전체를 가리킨다. 게시는 request_id를 요구하지 않는다.
   Report 저장의 request_id 재시도와 다른 규칙이며, 베타의 재시도 보장은 이 둘이다(ADR-0006).
   - 대상에 같은 `publication_id`의 버전이 있고 manifest와 manifest가 적은 모든 파일이 바이트까지 같으면, 다시 쓰지 않고 그
     버전을 재사용해 전환만 한다.
   - 같은 ID에 내용이 다르면 덮어쓰지 않고 거절한다. manifest에 없는 파일(OS나 동기화 프로그램이 만드는 `.DS_Store`·`desktop.ini`
     등)은 이 판단에 쓰지 않고 receipt에 관찰로 남긴다.
   - 되돌리기는 그 channel의 버전 목록에 있는 버전을 manifest와 다시 대조한 뒤에만 포인터를 바꾼다.
   - 게시는 원본 프로젝트를 다시 읽지 않는다. 빌드의 파일이 게시 도중 바뀌면 확인에서 해시가 맞지 않아 전환하지 않는다.
   - 버전 삭제는 지원하지 않는다(사람이 지운다). 이미 복사·동기화·다운로드된 사본은 회수할 수 없다(공유 설계 §6 게시 중단).
7. **게시 시도마다 Publish receipt 하나를 프로젝트 기록으로 남긴다.** 기록 가족·kind·필드 이름은 ADR-0006의 공통 형식을 따르고,
   새 이름은 [용어집](../TERMINOLOGY-AND-LANGUAGE.md)에 먼저 등록한다(용어집 §8).

   | 내용 | 기준 |
   |---|---|
   | 게시본 | `publication_id`와 패키지 해시. 빌드의 기록 기준은 빌드의 manifest에 있고 receipt는 `publication_id`로 가리킨다 |
   | 대상 | 저장한 대상 이름(있으면)과 그때 쓴 폴더 경로. 프로젝트 밖 경로의 표기는 ADR-0006을 따른다 |
   | 시각 | 시작과 끝 |
   | 단계별 상태 | 전송·확인·확정·전환 각각의 완료·실패·재사용. 실패한 단계·파일·링크와 이유 |
   | 주소 | 버전 주소, 최신 주소와 channel, 전환 전의 최신 `publication_id` |
   | 확인한 접근 범위 | 결정 8에서 실제로 확인한 것만 |
   | 포함한 Report 버전 | 빌드가 담은 `report_id + revision_id`(ADR-0006의 참조 형태). `publication_id`와 따로 적는다 |

   - receipt는 시도가 끝날 때(성공·실패) 쓴다. 대상의 파일이 먼저이고 receipt가 마지막이다(ADR-0006의 커밋 규칙). 강제 종료로
     receipt가 없으면 대상의 상태(남은 staging, 확정된 버전, 포인터)가 사실이며 다음 게시가 그 상태에서 잇는다.
   - 다른 독자가 같은 폴더를 여는 주소(다른 기기의 마운트 경로, UNC 경로, 폴더를 내보내는 HTTP 주소)는 analytics가 알 수 없다.
     추측해 적지 않는다.
8. **접근 범위는 폴더의 권한이다.** analytics는 화면·CLI·receipt 어디에도 대상을 "팀 전용"이라고 쓰지 않고, 그 폴더를 열 수 있는
   사람이 읽을 수 있다고 적는다. receipt에는 실제로 확인한 것만 남긴다.
   - 게시한 계정이 대상에서 다시 읽은 결과(결정 5의 확인).
   - POSIX에서 대상까지의 상위 디렉터리와 버전 디렉터리·파일의 그룹·기타 사용자 권한 비트(통과·읽기)의 관찰. 대상이 보고한
     값이며 ACL이나 서버 쪽 권한과 다를 수 있다. 다른 사용자가 읽을 수 없어 보이면 경고만 한다.
   - Windows ACL은 "확인 안 함"으로 적는다.

   권한을 바꾸지 않는다(chmod·ACL 변경 없음). 새 파일은 대상 폴더와 게시한 계정의 기본 권한을 받는다. 공개 범위를 자동으로 넓히지 않는다.
9. **#20의 패키지가 보장할 것.** 폴더 게시가 확인할 수 있도록 #20의 패키지와 manifest(공유 설계 §4의 `publication.json`, 이름은 #20이
   정함)는 다음을 지킨다.
   - 모든 파일의 목록과 각 파일의 sha256, entry `index.html`.
   - 패키지 안의 상대 링크만 쓴다. `../`로 패키지 밖을 가리키거나 절대 경로·`file://`·원격 CDN에 기대지 않는다(공유 설계 §4).
   - 모든 OS에서 쓸 수 있는 이름. Windows 예약 이름이 없고, 대소문자나 Unicode 정규화만 다른 두 이름이 없다. 심볼릭 링크가 없다.
   - `publication_id`는 그대로 디렉터리 이름이 될 수 있고, 대소문자를 구별하지 않는 대상에서도 다른 ID와 겹치지 않는다.
10. **검증.** 아래 "구현에서 확인할 것"을 #21의 수용 조건으로 삼는다. CI의 두 번째 계정과 HTTP 하위 경로는 대리 증거이고, 실제
    다른 독자의 열람은 [#24](https://github.com/handochan/aelix-analytics/issues/24) 리허설의 별도 증거다(공유 설계 §8: 실제 게시
    대상의 검증과 로컬 링크 검사는 서로 다른 증거).

## 대안과 선택 이유

| 대안 | 검토 결과 |
|---|---|
| GitHub Pages 브랜치 게시 | 실제 웹 주소가 생기고 ref 갱신이 원자적이다. 그러나 push 권한·토큰과 CI용 시험 저장소·secret이 필요하다. GitHub Free에서는 공개 저장소가 필요하고 Pages 사이트는 인터넷에 공개되므로 분석 데이터가 노출될 위험이 크다(배경). 배포가 늦어 E2E가 느리고 불안정하며, git 이력에 모든 게시본이 남아 원격 사본을 사실상 지울 수 없다 |
| S3 호환 저장소 | bucket 정책·서명 URL로 팀 전용을 줄 수 있다. 그러나 자격 증명이 필요하고, SDK 의존성이나 서명 구현이 의존성 없는 wheel과 부딪친다. CI 검증은 MinIO 컨테이너를 쓰는 Linux뿐이라 세 플랫폼 원칙([ADR-0002](0002-python-support-and-platform-scope.md))과 맞지 않고, 브라우저로 열려면 정적 호스팅 설정이 또 필요하다 |
| 최신 주소를 심볼릭 링크로 | 교체가 원자적이지만 Windows·SMB·동기화 폴더에서 만들 수 없거나 따라가지 않는다 |
| staging을 대상 밖(프로젝트, 임시 디렉터리)에 | 볼륨이 달라 확정이 원자적 이름 변경이 아니라 복사가 된다 |
| 같은 ID의 버전 디렉터리를 덮어쓰기 | 독자가 이미 연 버전 주소의 내용이 바뀐다. 버전 주소는 불변이다(공유 설계 §3) |
| 게시 재시도를 request_id로 판정 | 게시 단위는 이미 만든 `publication_id`와 해시다(공유 설계 §6). 같은 빌드는 어느 요청으로 다시 게시해도 같은 결과여야 한다 |
| 다른 사용자가 읽게 chmod | 공개 범위를 사용자 모르게 넓힌다 |
| 대상을 "팀 전용"으로 표시 | 폴더 대상에는 analytics가 확인할 수 있는 팀 권한 기능이 없다(공유 설계 §6 팀 접근) |
| 대상 경로를 프로젝트 기록에 둠 | 기기마다 경로가 달라 다른 기기에서는 틀린 설정이 된다 |
| 상시 `analytics_publish` 모델 도구 | 상시 도구 표면이 늘어난다(Q11). 게시는 사람의 명시적 요청으로 CLI·`/analytics`를 쓴다(공유 설계 §1) |

## 결과와 비용

- analytics는 웹 주소와 접근 제어를 제공하지 않는다. 팀이 웹 호스팅이나 SSO를 요구하면 P2의 adapter를 앞당긴다. `Publisher` 포트,
  receipt, staging과 전환 계약은 그대로 다시 쓰므로 되돌리는 비용은 adapter 하나다.
- 동기화 폴더는 다른 기기로 전파되는 순서를 보장하지 않는다. 다른 기기의 최신 페이지가 아직 도착하지 않은 버전을 잠시 가리킬 수
  있고, staging의 파일도 동기화될 수 있다.
- 네트워크 볼륨의 잠금, 동기화 폴더의 전파 순서, 같은 대상의 동시 게시는 검증하지 않는 범위다. 구현 PR이 현재 계약 G에 적고,
  `analytics publish`의 현재 동작을 현재 계약에, README의 "현재 공유 범위"를 함께 고친다.
- 공유 폴더를 비공개로 오해할 위험은 receipt와 화면 문구(결정 8)로 줄인다. 권한의 실제 범위는 폴더를 관리하는 사람이 정한다.
- receipt의 주소는 게시한 기기에서의 주소다. 다른 독자의 주소는 폴더를 여는 방법에 달려 있다.
- 실제 팀원 기기에서의 열람은 CI로 증명할 수 없다. 대리 증거와 리허설 증거를 따로 남긴다.
- #20의 패키지 규칙(결정 9)이 늘어난다. #20은 설계 단계부터 이 규칙을 따른다.

## 구현에서 확인할 것

#21의 수용 조건이다. 각 항목은 명령·환경·코드 기준·결과를 증거로 남기고, 실행하지 않은 것은 `not_run`으로 적는다.

1. CI Linux(필수): 두 번째 OS 계정이 버전을 file 경로와 HTTP 하위 경로(대상의 상위 폴더를 `python -m http.server`로 내보냄)로
   읽는다. HTML뿐 아니라 이미지·JS·첨부를 직접 읽는다. 다른 사용자가 읽을 수 없는 위치의 대상에서는 경고가 나오고 두 번째 계정의
   읽기가 실패하는 것도 확인한다.
2. macOS·Windows: 두 번째 계정을 만들어 같은 읽기를 시도하고 결과대로 기록한다. Windows에서는 루프백 SMB 공유(`New-SmbShare`)를
   거친 게시와 읽기도 측정한다. macOS는 저장소 CI의 macOS 실행(라벨·수동·주간)에서 돈다.
3. 세 OS: 전송·확인·확정·전환의 각 경계와 두 포인터 사이에서 중단을 주입한 뒤 최신 주소가 확인한 버전만 가리키는지, 재시도가
   재사용과 전환으로 끝나는지 확인한다.
4. 같은 빌드의 재시도는 재사용하고 같은 ID의 다른 내용은 거절하며, manifest에 없는 파일은 이 판단을 바꾸지 않는다. 되돌리기는
   다시 대조한 뒤에만 전환한다.
5. 게시 도중 원본 프로젝트나 빌드의 파일을 바꾼다. 게시는 원본을 다시 읽지 않고, 빌드가 바뀌면 전환하지 않는다.
6. 대상의 버전 바이트가 빌드와 같고, 대상에 심볼릭 링크가 없으며, 게시가 권한을 바꾸지 않는다. 이식할 수 없는 channel 이름은
   거절한다. 대상이 프로젝트 안이면 거절한다.
7. Windows에서 다른 프로세스(백신, 색인, 동기화 프로그램)가 연 파일 때문에 확정의 디렉터리 이름 변경이 거절되는 경우를 측정한다.
   `AtomicFiles`의 재시도 규칙으로 충분한지, 포트의 계약을 넓혀야 하는지는 그 결과로 구현 PR이 정한다(ADR-0003).
8. 잠금을 지원하지 않는 마운트에서의 동작을 측정해 receipt와 현재 계약 G에 적는다.
9. 실제 다른 독자의 열람(#24): 사람 검증은 소유자가 직접 한다. 소유자의 다른 기기나 다른 계정으로 연 결과는 그 기기·계정·경로
   그대로 적고, 팀원의 열람이나 팀 권한의 확인으로 옮겨 적지 않는다. 이 증거가 없으면 `not_run`이다.

## 관련

- [#21](https://github.com/handochan/aelix-analytics/issues/21): 이 결정과 구현. [#20](https://github.com/handochan/aelix-analytics/issues/20): P0 독립 build와 결정 9.
  [#22](https://github.com/handochan/aelix-analytics/issues/22): CI의 두 번째 계정·HTTP 하위 경로·SMB 루프백 단계.
  [#24](https://github.com/handochan/aelix-analytics/issues/24): 출시 리허설의 실제 다른 독자.
- ADR-0006([#4](https://github.com/handochan/aelix-analytics/issues/4)): receipt의 공통 기록 형식, 재시도 범위, 프로젝트 밖 경로의 표기.
  ADR-0007([#37](https://github.com/handochan/aelix-analytics/issues/37)): kit이 쓰는 텍스트의 UTF-8·LF.
- [ADR-0002](0002-python-support-and-platform-scope.md): 세 플랫폼. [ADR-0003](0003-portable-boundaries-and-in-process-kit-loading.md): 포트와 새 이름의 거절.
- [공유 설계](../PUBLISH-AND-SHARING.md) §3·§4·§6·§8, [베타 기준](../BETA-QUALITY-GATES.md) Q14·§4, [아키텍처](../ARCHITECTURE.md) §5,
  [용어집](../TERMINOLOGY-AND-LANGUAGE.md) §5(Publication, Publish receipt).
