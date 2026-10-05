# 메모리·skill 벤치마킹과 현재 구조 점검

최초 조사: 2026-09-17 · 추가 조사: 2026-09-18 · 범위: 공식 문서·소스·논문의 설계 비교와 로컬 코드 확인.
외부 시스템을 설치해 같은 분석 과제를 수행한 성능 벤치마크는 아니다. 논문의 성능 수치를 이 확장의 효과로 전용하지 않는다.

## 조사 기준

- 이 저장소: `7b205cd`. 작업 시작 시 README의 그래프 안내와 `PROPOSAL-EVIDENCE-GRAPH-2026-09-17.md`가 미커밋 상태였으며 둘 다 보존한다.
- Aelix: 인접 체크아웃 `026bd15db229d86d61b74fd3b8c1d8eb0f750715`의 확장 배포·skill 로더·프롬프트 코드를 확인했다. 아래 참조 대상 파일에는 로컬 변경이 없었다. 실제 배포 버전별 설치 검증은 별도다.
- Hermes: 공식 저장소의 `c712f06dcdd24053a4118f38d2090ac53137ecfc` 소스와 조사일의 공식 문서. 커밋 시각은 `2026-09-17T13:47:46Z`다. 움직이는 `main` 대신 아래 고정 링크를 구현 근거로 사용한다.
- Letta·Mem0·Graphiti: 조사일에 읽은 공식 문서·저장소의 구조 비교다. 실행 성능·모델 호환성을 실측하지 않았다.

## 현재 프로젝트에서 확인한 것

| 확인 | 근거 | 설계에 주는 의미 |
|---|---|---|
| 프로젝트·이벤트·run·데이터 스키마가 `/1`이고 기록이 파일로 남음 | [core.py](../src/aelix_analytics/core.py)의 스키마 상수, `read_events`, `list_runs` | 새 메모리 서버보다 기존 기록을 확장하는 편이 작음 |
| `finding`, `decision`, `assumption`과 질문·할 일, `resolves`가 있음 | `KEY_KINDS`, `OPEN_KINDS`, `add_note`, `resolved_targets` | 기본 지식 기록은 이미 존재. 정정·철회·적용 시점의 의미를 보강해야 함 |
| CONTEXT의 본문 일부를 넣고 나머지 섹션 제목·줄 범위를 제공함 | `context_selection`, `context_block` | 점진적 공개가 이미 출발해 있음 |
| 고정 섹션은 4,000자 예산을 넘겨도 들어가고, 생략 섹션 목록·journal 요약은 별도로 더함 | 동일 함수 | 4,000자는 전체 주입량의 상한이 아님. 아래 합성 재현 참조 |
| `refs.charts`는 논리적 차트 이름이고, HTML은 현재 갤러리로 연결 | [report.py](../src/aelix_analytics/report.py)의 `refs_html` | 과거 발견의 근거에는 정확한 버전 참조가 필요 |
| 평가 탭에 gap·기록 품질·도구 사용이 함께 있음 | `evaluation_section`, [report.js](../src/aelix_analytics/assets/report.js) | 자료를 버리지 않고 인사이트·재사용·진단 흐름으로 재배치 가능 |
| 도구 결과에 14,000자 제한과 이미지 최대 3개가 있음 | [확장 코드](../extension/aelix_analytics.py)의 `MAX_TEXT`, `MAX_IMAGES` | 문자·이미지 개수와 모델 토큰 비용을 구별해야 함 |
| 코드 스냅샷으로 주 스크립트를 재실행하지만 helper는 현재 경로에서 import | [runner.py](../src/aelix_analytics/runner.py)의 `_rerun_source`, `_exec_script`, `_run_script` | 라이브러리화 전에 재실행 의미와 환경 차이를 명시할 필요 |
| run·journal·report의 잠금은 있지만 여러 파일을 묶는 복구 계약은 없음 | `execute`, `_finalize`, `project_lock`, `report.build` | 직렬화와 트랜잭션·멱등성은 별개 |
| 리포트는 변경 감지 후 전체 run·데이터 페이지를 다시 생성 | `report._build_locked` | 조회 결과 제한만으로 대규모 생성 비용까지 해결되지는 않음 |
| 배포용 manifest·빌드 설정·LICENSE·기여 가이드·CI workflow가 작업 시작 시 없음 | 저장소 파일 목록, [pyproject.toml](../pyproject.toml)의 `package = false` | 공식 베타의 배포·기여 기준을 별도로 준비해야 함 |

현재 한계의 상세 내용은 [현재 계약](CONTRACTS.md)을 따른다. `inputs: []`만으로 도구 미사용·추적 실패·원인을 단정하지 않는다.

### 주입 예산의 작은 재현

현재 core만 로드해 임시 프로젝트를 만들었다. 분석 실행·네트워크·사용자 프로젝트 변경은 없다.

| 합성 입력 | 설정 | 관측 |
|---|---|---|
| `always` 섹션 하나와 ASCII `x` 20,000개 | CONTEXT 본문 예산 4,000자 | 선택된 본문 **20,029자**. 전체 블록은 이보다 큼 |
| 각 본문이 4,100자인 섹션 200개 | 동일 | 본문 0개를 선택해도 생략 목록 때문에 전체 블록 **10,000자 초과** |

재현 명령(개발 환경의 `.venv`가 있을 때):

```sh
.venv/bin/python - <<'PY'
import importlib.util
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("labcore_probe", "kit/labkit/core.py")
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)
cases = {
    "pinned": "## mandatory <!-- always -->\n" + "x" * 20000,
    "omitted_index": "\n".join(f"## section-{i}\n" + "x" * 4100 for i in range(200)),
}
for label, body in cases.items():
    preamble, included, omitted, _ = core.context_selection(body)
    with tempfile.TemporaryDirectory() as directory:
        project = core.Project(directory)
        project.context.write_text(body, encoding="utf-8")
        print(label, {
            "selected_chars": len(preamble) + sum(s["chars"] for s in included),
            "omitted_sections": len(omitted),
            "block_chars": len(core.context_block(project)),
        })
PY
```

전체 문자 수는 임시 경로 길이에 따라서도 달라지므로 임계값으로 적었다. 이는 문자 수 재현이며 실제 토큰 측정값이 아니다.

## Hermes에서 가져올 것과 바꿀 것

Hermes의 skill은 필요할 때 읽는 절차 문서다. 이름·설명 목록 → 본문 → 개별 참고 파일의 단계로 접근한다.
이 프로젝트에서도 같은 원칙을 쓰되, 목록을 작은 페이지부터 읽고 필요한 부분을 더 조회하게 한다.
토큰·문자 수 목표는 성능 측정 기준이며, 목표 초과를 이유로 조회를 중단시키는 제한으로 도입하지 않는다.
[공식 skill 문서](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/),
[고정 소스 `skills_list`, `skill_view`](https://github.com/NousResearch/hermes-agent/blob/c712f06dcdd24053a4118f38d2090ac53137ecfc/tools/skills_tool.py).

`skill_manage`는 파일 생성·수정·삭제를 제공하며, 선택적 쓰기 검토와 변경 기록 경로가 있다.
이 구조에서 **재사용할 경험의 선별, 기존 후보와의 통합, 변경 근거 보존**을 채택한다.
분석 확장에서는 첫 베타부터 자동 전역 등록을 복제하기보다, 후보와 검사 결과를 먼저 남긴다.
[고정 소스 `skill_manage`, `_record_success`](https://github.com/NousResearch/hermes-agent/blob/c712f06dcdd24053a4118f38d2090ac53137ecfc/tools/skill_manager_tool.py).

내장 메모리는 작은 파일의 세션 시작 스냅샷과 필요할 때의 과거 검색을 구별한다. 이는 상시 비용을 줄이는 참고가 된다.
다만 분석 중 정의·결론이 바뀌면 새 상태가 보여야 하므로, 여기서는 변경된 기록 리비전에서 요약을 갱신하는 방식을 제안한다.
Hermes의 메모리 쓰기 제한을 전체 프롬프트의 하드 상한으로 오해해서도 안 된다. 고정 소스는 외부에서 크게 수정한 메모리를 읽을 때 경고하고 유지한다.
[공식 메모리 문서](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/),
[고정 소스 `MemoryStore.load_from_disk`, `format_for_system_prompt`](https://github.com/NousResearch/hermes-agent/blob/c712f06dcdd24053a4118f38d2090ac53137ecfc/tools/memory_tool_store.py).

현재 `session_search`는 SQLite의 검색 결과와 실제 메시지를 반환하며, 조회 자체에 LLM을 호출하지 않는다.
검색·대화 읽기·주변 메시지 읽기라는 단계가 참고된다. README의 요약 설명이나 문서의 무절단 표현보다 구체적인 소스를 우선했다.
이 커밋에는 `content_truncated`와 긴 세션의 앞·뒤 부분 제공이 실제로 존재한다.
[고정 소스 `session_search`, `_shape_message`](https://github.com/NousResearch/hermes-agent/blob/c712f06dcdd24053a4118f38d2090ac53137ecfc/tools/session_search_tool.py).

**적용 판단:** Hermes의 학습 순환을 “기록 → 재발견 → 재사용 후보 → 검사 → 등록 → 사용 결과 반영”으로 가져온다.
모델 가중치 학습, 모든 세션의 백그라운드 재분석, 무제한 skill 카탈로그는 도입하지 않는다.
이는 제품의 비용·범위에 따른 설계 판단이며 Hermes의 분석 성능을 평가한 결론은 아니다.

## 메모리 시스템 비교

| 참고 대상 | 확인한 구조 | 채택할 부분 | 첫 베타에 직접 통합하지 않는 이유 |
|---|---|---|---|
| Hermes | 작은 상시 메모리 + 과거 검색 + 절차 skill | 가장 가까운 파일 중심 출발점, 단계적 조회 | 전체 에이전트 런타임은 Aelix와 중복. 분석 근거의 버전 연결은 따로 필요 |
| Letta / MemGPT | 붙어 있는 memory block과 바깥 메모리의 역할 구분 | 현재 작업 맥락과 상세 기록의 분리 | 별도 에이전트 서버가 기본 분석의 필요조건이 될 이유가 없음 |
| Graphiti / Zep | 사건·사실·관계를 시간과 함께 보존하고 조회 | 기록 시점과 내용의 적용 시점, 이전 판단의 보존 | 그래프 저장·추출 계층 전체보다 기존 journal의 명시적 관계가 작은 시작점 |
| Mem0 | 대화에서 메모리를 추출하고 범위 식별자·메타데이터로 검색 | 프로젝트 범위 제한, 추출물의 출처·관리 | 자동 추출만으로 수치·계산 근거의 정확성을 보장할 수 없고 추가 모델 비용 평가가 필요 |

Letta는 붙어 있는 블록이 지속적으로 문맥에 보이는 구조를 문서화한다. 이 확장에서는 목표·정의·현재 상태만 해당 역할을 맡기고,
전체 타임라인을 붙이지 않는다. [공식 memory block 문서](https://docs.letta.com/tutorials/attaching-detaching-blocks/),
[Letta memory schema](https://github.com/letta-ai/letta/blob/main/letta/schemas/memory.py).

Graphiti는 시간 이력과 사실의 유효 기간을 다루며, 그래프 저장소와 모델 기반 추출을 사용하는 구조다.
본 설계는 시점 구분을 참고하지만 그래프 DB 선택과 추론 추출을 필수로 삼지 않는다. 작은 모델의 구조화 출력도 별도로 확인해야 한다.
[공식 저장소](https://github.com/getzep/graphiti), [시간 관계 처리 코드](https://github.com/getzep/graphiti/blob/main/graphiti_core/utils/maintenance/edge_operations.py).

Mem0의 조사일 문서는 `add`를 누적 저장으로 설명하고, 별도의 `update`가 기존 내용을 바꾼다고 설명한다.
과거 버전의 ADD/UPDATE/DELETE 자동 판단 설명을 현재 계약처럼 옮기지 않았다. 이 프로젝트는 정정 이벤트로 원래 발견을 보존한다.
[공식 Add](https://docs.mem0.ai/core-concepts/memory-operations/add), [공식 Update](https://docs.mem0.ai/core-concepts/memory-operations/update).

**선택:** 기존 journal·CONTEXT·run을 정본으로 두고, Python 표준 `sqlite3` 기반의 재생성 가능한 검색·관계 인덱스를 후보로 삼는다.
FTS5의 존재를 확인하고, 사용할 수 없으면 명시적인 제한 조회로 동작한다. 벡터 검색은 한국어·동의어 회수율을 측정한 뒤 검토한다.
FTS5의 unicode61은 한국어 형태소 이해를 약속하지 않으며, trigram도 세 글자 미만 검색에 한계가 있다.
따라서 “매출”, “성장”, “결측” 같은 짧은 한국어와 코드 식별자는 별도 필터·제한된 부분 문자열 검색을 함께 시험한다.
[SQLite FTS5 공식 문서](https://www.sqlite.org/fts5.html#tokenizers).

## 논문에서 취할 개념과 적용 한계

다음은 2026-09-17 최초 조사에서 선정한 기초 자료다. Hermes의 직접 기반 논문이라고 주장하지 않는다.
기존 참고문헌을 유지하고, 사용자가 제시한 SkillOps와 최근 연구는 아래 2026-09-18 추가 조사에서 보강한다.

| 논문 | 관련 개념 | 이 프로젝트에 적용할 판단 |
|---|---|---|
| [Voyager, 2023](https://arxiv.org/abs/2305.16291) | 실행 가능한 skill 라이브러리와 실행 피드백을 통한 개선 | 설명만 저장하지 않고 검사 가능한 계산 코드를 연결. Minecraft의 결과를 분석 업무 효과로 환산하지 않음 |
| [MemGPT, 2023/2024](https://arxiv.org/abs/2310.08560) | 작은 문맥과 바깥 저장소 사이의 계층적 메모리 관리 | 상시 요약·검색 결과·상세 근거를 분리. 긴 문맥 지원을 가정하지 않음 |
| [MemSkill, 2026](https://arxiv.org/abs/2602.02474) | 기억을 추출·통합·정리하는 절차 자체를 재사용·개선 | 실패 사례를 다음 절차 개정의 근거로 사용. 학습된 controller·designer를 베타에 구현하는 것은 아님 |

이 표는 논문의 초록·공식 소개에서 확인한 설계 개념의 비교다. 학습 코드 재현이나 논문 실험의 독립 검증은 수행하지 않았다.

## Aelix와의 실제 접점

Aelix는 skill의 이름·설명·위치를 노출하고 본문은 필요할 때 읽는 경로를 이미 갖고 있다.
새로운 전역 skill 시스템을 확장 안에 중복 구현할 필요가 없다. 다만 등록된 설명 목록도 입력 비용이므로 후보를 모두 설치해서는 안 된다.
[고정 Aelix skill 프롬프트 소스](https://github.com/handochan/aelix-ai/blob/026bd15db229d86d61b74fd3b8c1d8eb0f750715/packages/aelix-coding-agent/src/aelix_coding_agent/cli/skills_prompt.py).

표준 skill은 `SKILL.md`와 선택적 scripts·references·assets로 구성된다. 계산 라이브러리와 skill을 연결하되,
장황한 실행 로그를 skill 본문에 복사하지 않는 방향이 적합하다. [Agent Skills 명세](https://agentskills.io/specification).

공식 catalog에 등재하는 패키지는 설치 결과에서 manifest가 결합되어야 한다. 현재 파일 확장은 그대로 사용할 수 있지만,
공식 배포 후보에는 wheel 안의 코드·manifest·template·CSS·JS 포함과 실제 설치 경로를 검증해야 한다.
`verify` 성공은 배포 결합 확인이지 보안·분석 품질 인증이 아니다. 등록 endpoint가 없는 경우의 성공 코드도 걸러야 한다.
[Aelix ADR-0207](https://github.com/handochan/aelix-ai/blob/026bd15db229d86d61b74fd3b8c1d8eb0f750715/docs/decisions/0207-a-catalog-listed-pack-must-bind-a-manifest.md).

현재 관측, 채택 제안, 미검증 목표를 구분해 [설계](DESIGN-CONTINUITY-AND-REUSE.md)와 [출시 기준](BETA-QUALITY-GATES.md)으로 이어 간다.

## 2026-09-18 추가 조사

추가 범위는 skill의 제안·유지관리·선택, 장기 맥락의 선별·정정, 도구 표면 증가의 영향이다.
2026-09-18까지 공개된 자료의 arXiv 원문·초록을 확인했다. 검색 결과의 수집일과 논문의 최초 공개·개정일을 구별한다.
아래는 이번 설계와 직접 관련된 최근 자료이며, 모든 최신 논문을 망라했다거나 성능을 독립 재현했다는 뜻은 아니다.

| 추가 자료·확인 버전 | 공개·개정일 | 읽은 범위와 채택할 관점 | 적용 한계 |
|---|---|---|---|
| [SkillOps — Pu et al., v1](https://arxiv.org/abs/2605.13716v1) | 2026-05-13 | 초록·본문·한계·부록 G. 구조화된 skill 계약과 라이브러리 유지관리, 중복 통합·검사 보강·퇴역 | 주로 ALFWorld·부분 합성 라이브러리, 규칙 기반 유지관리 stub. 분석 라이브러리의 자동 수리 효과로 일반화하지 않음 |
| [MindMemOS, v1](https://arxiv.org/abs/2608.12428v1) | 2026-08-12 | 초록·본문의 skill 진화 사례·향후 과제. 메모리의 수정 피드백, 작업 결과를 통한 절차 개정 | 파일·문서의 버전·provenance를 일급 대상으로 다루는 확장은 향후 과제에 포함. 설치만으로 본 프로젝트의 근거 추적이 해결되는 것은 아님 |
| [Dual-Layer Agentic Memory, v2](https://arxiv.org/abs/2608.22215v2) | 최초 2026-08-23, 개정 2026-08-31 | 초록. 저장하지 않음·새로 저장·갱신을 구별하는 비용 고려형 메모리 수명주기 | 모델 cascade·파라미터 학습은 베타 범위 밖. 초록상 코드·데이터 공개는 채택 후 예정으로, 구현을 검증한 자료가 아님 |
| [MASkills, v1](https://arxiv.org/abs/2609.02094v1) | 2026-09-02 | 초록. 사용 결과에 따라 skill을 개정·통합·정리하고 기여를 평가 | 다중 에이전트 최적화 설정. 베타에 상시 백그라운드 에이전트나 학습기를 추가하는 근거로 쓰지 않음 |
| [Beyond Top-k Skill Retrieval / DSR, v1](https://arxiv.org/abs/2609.05824v1) | 2026-09-05 | 초록. 관련성뿐 아니라 중복을 줄이고 서로 보완되는 skill 묶음을 선택 | SkillRouter의 routing 결과. 특정 reranker·DPP를 기본 의존성으로 채택하거나 분석 효과를 보장하는 것은 아님 |

동명 자료와 혼동하지 않도록 이 문서의 SkillOps는 **arXiv:2605.13716**을 가리킨다.
실행할 때의 비용과 라이브러리 유지관리 비용을 구별하는 개념을 채택한다.
논문은 유지관리의 효과가 기존 에이전트 방식에 따라 다르고 자체 복구와 충돌할 수도 있다고 보고한다.
따라서 이 확장에서는 제안·유지관리의 옵션, 기존 후보와의 중복 확인, 버전별 검사 기록을 먼저 설계한다.
[SkillOps 본문·한계·규칙 기반 유지관리](https://arxiv.org/html/2605.13716v1).

최근 메모리 연구가 강조하는 선택적 저장은 모든 기록을 지우거나 요약으로 바꾸라는 뜻으로 적용하지 않는다.
이 확장에서는 원래 run·사건·발견을 보존하고, **어떤 항목을 상시 맥락·checkpoint·재사용 자산으로 올릴 것인가**를 선별한다.
정정과 사용자 피드백은 원본에 연결하며, 자동 생성한 요약이나 skill이 스스로 확정 사실이 되지 않게 한다.
이는 위 연구에서 얻은 제품 설계상의 판단이며, 해당 알고리즘을 그대로 구현한 결과가 아니다.

### 도구 표면과 프론티어 모델

[Scaling Enterprise Agent Routing, v1](https://arxiv.org/abs/2606.17519v1)(2026-06-16)은
110 agents·584 tools의 기업용 카탈로그에서 GPT-5.1·GPT-5.4·Claude Sonnet 4.5를 시험한다.
모호하게 표현된 요청의 단일 단계 routing에서 카탈로그 확대에 따라 F1이 16–23 percentage points 하락했다고 보고한다.
본문은 관련 후보를 찾지 못하는 문제와, 정답 후보가 있어도 혼동하는 문제를 구별한다.
[실험 설정·한계](https://arxiv.org/html/2606.17519v1).

이 결과는 의미가 겹치는 한 카탈로그에서의 관측이다. 도구를 6개에서 7개로 늘리면 항상 성능이 떨어진다거나,
모든 프론티어 모델에 공통 최적 개수가 있다는 증거로 확대하지 않는다.
후보를 먼저 줄이는 방식도 검색 품질에 따라 손해가 날 수 있으므로, 적은 도구라는 숫자만으로 성공을 판단하지 않는다.

[LongFuncEval](https://arxiv.org/abs/2505.10570)(2025)은 도구 목록·도구 응답·대화 길이의 부담을 함께 다루는 기초 참조로 추가한다.
최근 DSR의 skill 중복·보완성 문제와 결합해, **tool 수·schema tokens·분기 복잡도·선택 정확도·전체 과제 성공**을 함께 평가한다.
설계상의 결론은 새로운 기능을 곧바로 새로운 상시 도구로 만들지 않고, 기존 표면 활용과 선택적 노출을 우선 비교하는 것이다.

### Analytics Engine에서 확인한 후속 분석

인접 저장소 `aelix-analytics-engine`의 `860756691a21a7fcae1a0bd4b84de6449681b6b9`를 읽었다.
아래 계약·소스 파일에는 로컬 변경이 없었고, 다른 에이전트의 미커밋 문서에는 손대지 않았다.
계약 0.2의 오래된 상한 대신 **0.3**과 코드를 대조했다. 새 실행·브라우저 시험은 하지 않았다.

| 확인한 동작 | 근거 | 이 확장에 가져올 경계 |
|---|---|---|
| 저장 선택은 view·spec revision·dataset·input hash와 plot/source 행 ID에 묶임 | [계약 0.3 §2](https://github.com/handochan/aelix-analytics-engine/blob/860756691a21a7fcae1a0bd4b84de6449681b6b9/docs/contracts/03-core-contracts.md), `Project.select` | 화면 좌표만으로 후속 분석 입력을 정하지 않음 |
| 선택은 새 view의 `selection_id` 또는 transform 입력으로 사용됨 | [project.py](https://github.com/handochan/aelix-analytics-engine/blob/860756691a21a7fcae1a0bd4b84de6449681b6b9/src/aelix_analytics_engine/project.py), [worker.selection_filter](https://github.com/handochan/aelix-analytics-engine/blob/860756691a21a7fcae1a0bd4b84de6449681b6b9/src/aelix_analytics_engine/worker.py) | 원래 차트·선택 → 후속 실행 → 새 발견을 연결 |
| 집계 그룹은 대응 행으로 확장하고 파생 표의 선택은 그 파생 데이터셋 행을 뜻함 | 계약 0.3 §2 | 모든 선택을 원시 데이터 행으로 표현하지 않음 |
| summary·preview·lineage 등의 부분 조회와 행 페이지가 있음 | [surface.py](https://github.com/handochan/aelix-analytics-engine/blob/860756691a21a7fcae1a0bd4b84de6449681b6b9/src/aelix_analytics_engine/surface.py) | 후속 분석도 기존의 제한 조회 원칙 안에서 제공 |

실제 도입은 [선택 기반 후속 분석 설계](DESIGN-CONTINUITY-AND-REUSE.md#향후-선택-기반-후속-분석)의 F1·F2로 분리한다.
현재 이 확장의 첨부 표·최선 노력 계보만으로 Analytics Engine의 SelectionRef 보장을 제공할 수 있다고 주장하지 않는다.

## 문서 작업의 검증 범위

- Markdown의 로컬 파일·제목 링크, 코드 펜스와 JSON 예시를 검사했다.
- 2026-09-17에는 위 합성 재현 명령을 문서에서 추출해 실행했고, 고정 섹션과 생략 목록이 전체 주입량을 늘리는 것을 확인했다.
- 작업 시작 전 사본과 비교해 기존 그래프 제안·CONTRACTS 파일이 같은 바이트임을 확인했다. README에 있던 그래프 안내도 유지했다.
- 동작 코드는 변경하지 않았다. 외부 시스템 간 실행 성능, 실제 모델 토큰·재개 성공률, 새 화면의 브라우저 동작과 Mermaid 렌더링은 검증하지 않았다.

따라서 이 결과는 **현재 구조에 근거한 설계와 검증 계획**이다. 새 메모리·skill·UI 기능의 구현 완료나 베타 게이트 통과 증거는 아니다.
