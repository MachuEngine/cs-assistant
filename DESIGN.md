# CS 티켓 어시스턴트 — 설계 스펙

> 이커머스 CS 상담원을 보조하는 LLM 서비스. 포트폴리오 프로젝트.
> 목적: 분필(bunpil)에서 검증된 아키텍처(LangGraph ReAct + RAG + 가드레일 + eval)가
> **다른 도메인에도 일반화되는지 검증**하고, 그 위에 두 가지를 추가로 증명한다 —
> ① 벤더 통합 깊이(정식 경로 ↔ 커스텀 어댑터), ② 개발 과정 자체의 하네스/루프 설계.
>
> 기획 배경은 `CS_PROJECT_NOTES.md`, 개발 하네스는 `CS_CLAUDE_CODE_HARNESS_LOOP.md` 참고.
> 에이전트 행동 규칙은 `CLAUDE.md`, 빌드 절차는 `PROMPTS.md`.

**문서 상태**: 2026-07-28 데이터셋 실측 검증 완료. 개발 착수 가능.

---

## 0. 언어 정책 (2026-07-28 확정)

**파이프라인은 영어, 프로젝트 문서는 한국어.**

| 대상 | 언어 |
|---|---|
| 티켓 입력(Bitext), 합성 정책 문서, LLM 프롬프트, 생성 초안, Judge 루브릭 | **영어** |
| 설계 문서, 커밋 메시지, 코드 주석, UI 레이블 | **한국어** |

**근거**: Bitext 데이터셋은 **영어 전용**이다(4절 검증 결과). 인텐트 라벨이 이 프로젝트의 유일한 외부 ground truth인데, 번역을 끼우면 (a) 번역 품질이 라벨과 어긋날 때 정확도 하락의 원인이 모델인지 번역인지 분리 불가능해지고, (b) 26,872건 번역 비용·시간이 든다. 정책 문서까지 영어로 통일하면 RAG 검색 질의·코퍼스가 같은 언어라 임베딩 성능도 자연스럽다.

**따라 오는 결정**: PII 마스킹은 **영문 패턴**(이메일·전화·신용카드·주소)을 대상으로 한다. 분필의 한국어 마스킹(주민번호·학교명)은 이식하지 않는다.

---

## 1. 개요

- **목적**: 고객 문의 티켓을 받아 (1) 유형을 분류하고, (2) 사내 정책·주문 정보를 근거로 **답변 초안**을 생성해 상담원에게 제시
- **사용 맥락**: 포트폴리오(Agent·RAG·평가·벤더 통합·하네스 실습). 실사용자 확보 여부는 미정(14절)
- **핵심 포지션**: **자동 응답이 아니라 상담원 증강(human-in-the-loop)**. 모든 출력은 상담원 검토 대상이며, 확신도가 낮으면 초안을 만들지 않고 사람에게 에스컬레이션한다
- **모듈**: ① 티켓 분류(Triage), ② 답변 초안 생성(ReAct Agent)
- **데이터 원칙**: 실제 고객 데이터 미사용. 공개 데이터셋(Bitext) + 합성 정책 문서 + 합성 주문 DB

### 도메인 범위 — "얇게" 특화한 이커머스

특정 버티컬(예: 패션 커머스)로 좁게 못 박지 않는다. 좁히면 (a) 도메인 실무 지식 부족으로 eval 신뢰도가 떨어지고, (b) 포트폴리오 평가 축이 "도메인 지식"으로 오해된다. 실제 축은 **ReAct + RAG + 가드레일 + eval 설계 역량**이다.

대신 tool·정책·eval을 **구체적으로 정의할 수 있을 만큼만** 좁힌다 → 이커머스가 적합(주문조회·반품/교환·배송 tool이 직관적이고, 정책 위반 정의가 명확하며, 본인이 검증 가능).

**verticalization을 구조로 차용**: Bitext는 스스로를 "customer service verticalization용"으로 규정하며, 특정 산업에 종속되지 않는 공통 인텐트만 담고 있다(4절). 이 2단계 설계를 그대로 프로젝트 구조로 가져온다.

1. 공통 인텐트 27개로 CS 에이전트 **기본 동작** 구현
2. 그 위에 **얇은 이커머스 특화 레이어**를 얹는다 (합성 정책 문서 + 도메인 tool + 도메인 eval)

→ 프레이밍: "이커머스 CS를 만들었다"가 아니라 "**범용 CS 파이프라인을 도메인에 특화시키는 과정을 재현했다**".

---

## 2. 아키텍처

### 전체 흐름

```
티켓 입력 → PII 마스킹 → [모듈 ①] Triage(인텐트·카테고리·confidence)
                              │
                    에스컬레이션 조건 해당 ─→ escalate(초안 없음, 사유만)
                              │
                              ▼
                   [모듈 ②] ReAct Agent ↔ tools(주문/정책/등급)
                              │ submit_for_review
                              ▼
                   judge_node (별도 벤더 LLM: 정책 준수 + 톤 채점)
                              │
                              ▼
                   validate_node (코드가 threshold·근거인용·PII 판정)
                              │
                 ┌────────────┼────────────┐
              통과          미달·budget>0   미달·budget=0
                 │              │              │
                 ▼              ▼              ▼
          상담원 검토 초안    agent 재시도    escalate(사람)
```

### 모듈 ① 티켓 분류 (Triage)

가장 단순하고, 가장 깨끗하게 측정 가능한 모듈. Bitext 인텐트 라벨이 곧 ground truth라 eval이 명확하다.

- **입력**: 마스킹된 티켓 본문
- **출력**: `{intent, category, confidence, requires_human, reason}`
- **구현**: 단일 LLM 호출 + structured output(구조화 스키마 강제). ReAct 불필요 — **에이전트가 필요 없는 곳에 에이전트를 쓰지 않는다**는 것도 설계 판단이다
- **인텐트/카테고리**: Bitext의 27개 인텐트 / 11개 카테고리를 그대로 사용(4절 목록)
- **`requires_human` 판정**: LLM이 `confidence`를 **기록**하고, 임계값 통과 여부는 **코드가 결정**한다 (설계 원칙 1)

### 모듈 ② 답변 초안 생성 — ReAct Agent (LangGraph)

분필 `exam` 모듈의 구조를 이식한다. 노드 순서: `plan → agent → judge → validate → (retry | escalate | end)`.

**도구(Tools)** — 모든 도구는 LLM 호출 없이 **순수 계산·검색·저장**만 수행한다. 추론과 문장 생성은 에이전트(LLM)가 직접 담당한다(도구 안에 LLM을 중첩하는 안티패턴 금지 — 분필에서 확립한 원칙).

| 도구 | 역할 | 구현 |
|---|---|---|
| `search_policy` | 반품·교환·환불·배송 정책 조항 검색 | ChromaDB + Rerank |
| `lookup_order` | 주문 상태·배송 추적 조회 | 합성 주문 DB(SQLite) |
| `check_customer_tier` | 고객 등급 조회(등급별 정책 분기용) | 합성 고객 DB |
| `check_live_notices` | 운영자가 작성한 라이브 공지 조회(Phase 12a) — 정적 정책 코퍼스가 못 다루는 "지금 유효한 정보" | MCP `NoticeSource`(읽기·멱등, async) |
| `validate_draft_format` | 초안 형식 검증(인사·본문·마무리, 길이) | 함수 |
| `save_draft` | 초안 저장 — 결정론적 게이트 통과 시에만 | 함수 |
| `discard_draft` | 초안 폐기(교체 시) | 함수 |
| `escalate_to_human` | 스스로 처리 불가 판단 시 명시적 에스컬레이션 신호 | 함수 |
| `submit_for_review` | 작성 완료 신호(인자 없음) | 함수 |

> `check_live_notices`만 `async def`다 — MCP 클라이언트가 async라 `agent_node`는 모든 도구를
> `ainvoke`로 호출한다(Phase 12a, 기존 8개 동기 도구도 LangChain이 투명하게 지원).

**`save_draft`의 결정론적 게이트 6종**

| # | 검사 | 거부 조건 |
|---|---|---|
| ① | **PII 재유출** | 초안에 **마스킹되지 않은 원본 PII 패턴**(이메일·전화·카드번호·주소)이 나타남. 마스킹 토큰(`{{EMAIL}}`) 자체는 허용 |
| ② | **근거 없는 확약** | `search_policy`/`lookup_order`/`check_live_notices`(활성·scope 일치분만)가 반환한 적 없는 금액·날짜·환불 확약이 초안에 포함 |
| ③ | **금지 표현** | 규칙 기반 블랙리스트 — 법적 확약(`guarantee`, `we are liable`), 타사 비방, 무조건 보상 약속 |
| ④ | **정책 인용 존재** | 정책 인용이 **필수**인 인텐트(3절 매핑)인데 인용된 조항이 0건 |
| ⑤ | **상담원 최종 책임 고지** | "This is a draft prepared by an AI assistant..." 고지 문구가 초안에 그대로 없음(로컬 모델이 프롬프트 지시를 빼먹는 사례가 실측되어, 프롬프트 신뢰 대신 게이트로 강제하기로 결정 — 2026-07-28) |
| ⑥ | **라이브 공지 인지 누락**(Phase 12a) | 공지 조회 필수 인텐트인데 `check_live_notices` 미호출(`NOTICE_SOURCE=noop`이면 이 조건 미적용), 또는 활성·scope 일치 공지(`grounded_notices`)가 있는데 `applied_notices`가 이를 전부 포함하지 않음 |

> **게이트 ⑥이 강제하는 것은 "반영"이 아니라 "인지"다.** 게이트 ④(정책 인용)는 조항 ID가
> 초안 **본문에** 있는지 검사하지만, 게이트 ⑥은 `applied_notices` 인자에 `notice_id`가
> 있는지만 본다 — 초안 본문과 공지 내용을 대조하지는 않는다. 의도된 설계다: 활성·scope
> 일치 공지라도 이 티켓에 실제로 무관할 수 있고, 그때 억지로 본문에 넣게 하면 관련 없는
> 정보를 끼워넣는 유인이 생긴다(게이트 ④를 인용 필수 인텐트로 한정한 것과 같은 논리).
> 대신 프롬프트가 "반영하지 않기로 했으면 그 이유를 설명하라"고 지시한다.
> **한계로 기록**: 모델이 `applied_notices`에 id만 넣고 본문에 아무것도 안 써도 게이트는
> 통과한다. 이걸 강제하려면 공지 본문과 초안의 의미 대조가 필요한데, 그건 결정론적
> 게이트가 아니라 judge의 일이다.

거부 시 사유를 도구 응답으로 되돌려 에이전트가 스스로 교정하게 한다(자기교정 루프).

**노드 책임 분리**

| 노드 | 주체 | 판단하는 것 |
|---|---|---|
| `agent` | 생성 LLM | 어떤 도구를 몇 번 부를지, 초안 문장 작성, 제출 시점 |
| `judge` | **별도 벤더 LLM** | 정책 준수 점수, 톤 점수, 위반 항목 나열 |
| `validate` | **코드** | judge 점수의 threshold 통과, 근거 인용 존재, PII 검사, 재시도/에스컬레이션 결정 |

> **judge를 별도 노드·별도 벤더로 두는 이유** — 분필에서 얻은 교훈이다.
> 분필은 처음에 생성 에이전트가 `similarity_judge` 도구로 자기 출력을 스스로 채점(self-judge)했는데,
> 그 신뢰도는 사람 라벨과 한 번도 대조된 적이 없었고, 정작 오프라인 eval이 검증하는 Judge와
> 런타임에 배포된 Judge가 **서로 다른 코드 경로**였다(검증-배포 불일치).
> CS 프로젝트는 처음부터 `judge_node`가 오프라인 eval과 **동일한 `app/modules/reply/judge.py`의 함수**를
> 호출하도록 설계한다. 그래야 EVAL.md의 Judge 신뢰도 수치가 곧 배포된 judge의 신뢰도다.
> 정책 위반·톤은 분필의 구조 유사도보다 **주관성이 강해** 이 분리가 더 중요하다.

**State**

```python
class ReplyState(TypedDict):
    ticket: dict          # {ticket_id, text(마스킹 후), customer_id, ...}
    triage: dict          # {intent, category, confidence, requires_human}
    draft: dict           # {reply_text, cited_policies[], tools_used[]}
    judge_result: dict    # {policy_compliance, tone, violations[], reasoning}
    validation_passed: bool
    validation_feedback: str
    budget: int           # 남은 재시도 횟수
    outcome: str          # "auto_draft" | "escalated" | "failed"
    escalation_reason: str
```

### HITL — 세 가지 종료 상태

분필은 "통과 / 예산 소진 후 종료" 2상태였지만, CS는 **에스컬레이션을 1급 종료 상태로 둔다.** 이게 실제 프로덕션 CS 패턴(컨피던스 임계값 라우팅)과 일치한다.

| `outcome` | 조건 | 상담원이 보는 것 |
|---|---|---|
| `auto_draft` | 에스컬레이션 조건 미해당 & judge 통과 & 코드 검증 통과 | 초안 + 인용 정책 + 사용 도구 |
| `escalated` | 3절 에스컬레이션 조건 중 하나라도 해당 | **초안 없음** + 에스컬레이션 사유 |
| `failed` | 파이프라인 예외 | 오류 표시(내부 상세 비노출) |

**초안이 없는 것이 잘못된 초안보다 낫다.** budget 소진 시 마지막 미달 초안을 그냥 내보내지 않는다.

---

## 3. 라우팅 규칙 (구현에 직접 쓰이는 표)

### 3.1 에스컬레이션 기준

아래 중 **하나라도** 해당하면 `escalated`. 판정은 전부 **코드**가 한다.

| ID | 조건 | 판정 시점 |
|---|---|---|
| E1 | `triage.confidence < TRIAGE_CONFIDENCE_THRESHOLD` | triage 직후 |
| E2 | `intent == contact_human_agent` (고객이 명시적으로 사람을 요청) | triage 직후 |
| E3 | `intent == complaint` (보상·책임 판단이 섞임) | triage 직후 |
| E4 | Bitext `flags`에 `W`(offensive language) 포함 | triage 직후 |
| E5 | 에이전트가 `escalate_to_human` 호출 | agent 중 |
| E6 | `lookup_order`가 해당 주문을 못 찾음 | agent 중 |
| E7 | `save_draft` 게이트를 `SAVE_DRAFT_FAIL_STREAK`회(기본 3) 연속 통과 못함 | agent 중 |
| E8 | `budget` 소진 후에도 `validate` 미통과 | validate 후 |
| E9 | 공지 조회 필수 인텐트(3.2절)인데 `check_live_notices` 조회가 실패(Phase 12a) | agent 중 |

> E3(complaint)를 자동 초안 대상에서 뺀 이유: 불만 티켓은 보상 여부·금액 판단이 섞이는데, 이건 정책 문서만으로 결정되지 않는 **제품 판단**이다. 초깃값으로 전량 에스컬레이션하고, 에스컬레이션 FP율이 과하면 그때 세분화한다.
>
> E9 우선순위는 `E6 > E9 > E5 > E7`이다 — E6이 `lookup_order`가 실제로 확인한 결정론적 사실을 최우선하는 것과 같은 논리로, 더 구체적인 근본 원인을 먼저 본다(Phase 12a). `NOTICE_SOURCE=noop`(기능 비활성)은 E9이 아니다 — 조회를 시도했는데 실패한 경우에만 해당한다.

### 3.2 인텐트 → 도구 매핑

`save_draft` 게이트 ④(정책 인용 필수 여부)와 프롬프트의 도구 안내가 이 표를 쓴다. "공지" 열은
게이트 ⑥(라이브 공지 반영, Phase 12a)이 참조하는 `NOTICE_REQUIRED`다.

| 카테고리 | 인텐트 | `search_policy` | `lookup_order` | `check_customer_tier` | 공지 |
|---|---|:---:|:---:|:---:|:---:|
| ORDER | `cancel_order` | **필수** | **필수** | 선택 | 선택 |
| ORDER | `change_order` | **필수** | **필수** | — | — |
| ORDER | `place_order` | 선택 | — | 선택 | 선택 |
| ORDER | `track_order` | — | **필수** | — | **필수** |
| CANCEL | `check_cancellation_fee` | **필수** | **필수** | **필수** | 선택 |
| REFUND | `check_refund_policy` | **필수** | — | 선택 | 선택 |
| REFUND | `get_refund` | **필수** | **필수** | **필수** | 선택 |
| REFUND | `track_refund` | 선택 | **필수** | — | **필수** |
| DELIVERY | `delivery_options` | **필수** | — | **필수** | **필수** |
| DELIVERY | `delivery_period` | **필수** | 선택 | — | **필수** |
| SHIPPING | `change_shipping_address` | **필수** | **필수** | — | **필수** |
| SHIPPING | `set_up_shipping_address` | 선택 | — | — | 선택 |
| PAYMENT | `check_payment_methods` | **필수** | — | 선택 | **필수** |
| PAYMENT | `payment_issue` | 선택 | **필수** | — | **필수** |
| INVOICE | `check_invoice` / `get_invoice` | 선택 | **필수** | — | — |
| ACCOUNT | `create_account` / `delete_account` / `edit_account` / `switch_account` / `recover_password` / `registration_problems` | — | — | — | — |
| SUBSCRIPTION | `newsletter_subscription` | — | — | — | — |
| FEEDBACK | `review` | — | — | — | — |
| FEEDBACK | `complaint` | — | — | — (E3: 에스컬레이션) | — |
| CONTACT | `contact_human_agent` / `contact_customer_service` | — | — | — (E2: 에스컬레이션) | — |

- **필수** = 해당 도구를 호출하지 않고 저장하면 `save_draft`가 거부
- ACCOUNT/SUBSCRIPTION/FEEDBACK/CONTACT 계열은 **절차 안내**라 정책 조항 인용이 필요 없다 — 여기까지 인용을 강제하면 없는 근거를 만들어내는 유인이 생긴다
- **공지 필수 7개**: `delivery_period` `delivery_options` `track_order` `track_refund` `payment_issue` `change_shipping_address` `check_payment_methods`. PROMPTS.md Phase 12 표 원안은 6개였으나, `check_payment_methods`가 버킷에서 누락된 것을 사람에게 확인해 필수로 추가 확정했다(2026-07-30) — 결제수단 FAQ성 인텐트지만 결제 관련 실시간 이슈(예: 특정 카드사 장애)의 영향을 받을 수 있다고 판단

### 3.3 파라미터 초깃값

**전부 초깃값이며 측정 후 조정한다.** 환경변수로 노출해 코드 수정 없이 바꿀 수 있게 한다.

| 파라미터 | 초깃값 | 근거 |
|---|---|---|
| `TRIAGE_CONFIDENCE_THRESHOLD` | **0.70** | LLM self-reported confidence는 과신 경향이 있어 낮게 잡으면 에스컬레이션이 폭증한다. 0.70에서 시작해 **캘리브레이션 곡선(정분류/오분류 confidence 분포)** 측정 후, 에스컬레이션 Recall ≥ 0.9를 만족하는 **최소** 임계값으로 조정 |
| `REPLY_TURN_CAP` | **12** | agent 노드 내부 LLM 왕복 상한. 정상 경로는 정책검색 1–2 + 주문조회 1 + 형식검증 1 + 저장 1 + 제출 1 ≈ 6회. 자기교정 여유를 포함해 2배. (분필은 문항 세트라 14였음) |
| `REPLY_BUDGET` | **2** | validate 미달 시 agent 재시도 횟수. 단일 초안이라 3회차가 유의미하게 나아진다는 근거가 없다 — 그럴 바엔 에스컬레이션이 HITL 원칙과 일관 |
| `MALFORMED_TOOL_CALL_STREAK` | **3** | tool_call 형식이 깨졌을 때 재작성 요청 연속 허용 횟수(분필과 동일). turn cap이 항상 최종 방어선 |
| `SAVE_DRAFT_FAIL_STREAK` | **3** | 에스컬레이션 E7("save_draft 게이트 3회 연속 실패") 임계값. `MALFORMED_TOOL_CALL_STREAK`와는 별개 개념(도구 호출 JSON 형식 오류 vs 저장 게이트 내용 거부)이라 별도 변수로 분리(2026-07-28, Phase 6 구현 중 발견 — 원래 3.1절엔 "3회 연속"만 서술되고 상수명이 없었음) |
| `JUDGE_PASS_POLICY` | **≥ 4 / 5** | validate 통과 조건 |
| `JUDGE_PASS_TONE` | **≥ 4 / 5** | validate 통과 조건 |
| 청킹 크기 | **300–500 토큰, overlap 50** | 조항 단위를 우선하되 500 초과 시 문장 경계로 분할. 각 청크 앞에 **조항 헤더를 반복 삽입**한다(인용 정확도가 게이트 ④에 직결) |
| 검색 `top_k` | **정책 3, rerank 전 10** | 분필과 동일 |
| `NOTICE_DEFAULT_TTL_DAYS` | **14** | Phase 12a — `valid_until`이 공란인 공지의 기본 유효기간(`valid_from`부터 일수, 양쪽 포함) |
| `NOTICE_MAX_COUNT` | **5** | Phase 12a — `check_live_notices` 한 번에 반환하는 공지 건수 상한(컨텍스트 폭주 방지) |
| `NOTICE_MAX_BODY_CHARS` | **500** | Phase 12a — 공지 본문 길이 상한(초과분은 절단) |

### 3.4 Judge 루브릭 · 출력 스키마

`app/modules/reply/judge.py` — 런타임 `judge_node`와 오프라인 eval이 **같이 호출**한다.

```json
{
  "policy_compliance": 1,
  "tone": 1,
  "violations": [
    {"type": "unsupported_commitment", "span": "...", "severity": "high"}
  ],
  "reasoning": "..."
}
```

| 필드 | 척도 | 정의 |
|---|---|---|
| `policy_compliance` | 1–5 | 초안의 주장이 **인용된 정책 조항·도구 결과로 뒷받침되는가**. 5=전부 뒷받침, 3=일부 미확인, 1=정책과 모순 |
| `tone` | 1–5 | CS 응대 톤 적절성(공감·명확성·격식). 5=바로 발송 가능, 3=수정 필요, 1=부적절 |
| `violations[].type` | enum | `unsupported_commitment` / `policy_contradiction` / `missing_citation` / `inappropriate_tone` / `pii_leak` / `out_of_scope_promise` |
| `violations[].severity` | enum | `high` / `medium` / `low` |

**validate 통과 조건**: `policy_compliance ≥ 4` **AND** `tone ≥ 4` **AND** `high` severity 위반 0건.

> 루브릭 텍스트는 `prompts/judge_*.md`에 두고 버전 관리한다. 루브릭 변경은 **단독 커밋**으로 분리한다 — 코드와 섞이면 점수 변화의 원인을 분리할 수 없다.

---

## 4. 데이터

### 4.1 Bitext 데이터셋 (실측 검증 — 2026-07-28)

[Bitext Customer Support LLM Chatbot Training Dataset](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset)

| 항목 | 확인된 값 |
|---|---|
| 규모 | **26,872** 질문-답변 쌍 (인텐트당 약 950~1,000건 — **분포 균등**) |
| 인텐트 | **27개** |
| 카테고리 | **11개** — `ACCOUNT` `CANCEL` `CONTACT` `DELIVERY` `FEEDBACK` `INVOICE` `ORDER` `PAYMENT` `REFUND` `SHIPPING` `SUBSCRIPTION` |
| 컬럼 | `flags` · `instruction` · `category` · `intent` · `response` |
| **언어** | **영어 전용** |
| 라이선스 | **CDLA-Sharing-1.0** (share-alike) |
| 엔티티 | `instruction` 컬럼에 등장하는 플레이스홀더 **9종** (아래 표) |
| 성격 | hybrid synthetic — NLG로 확장 후 전산언어학자가 큐레이션 |

> ⚠️ **2026-07-28 재정정**: 이전 버전은 카테고리를 10개로, 이름을 다르게(`CANCELLATION_FEE`·`SHIPPING_ADDRESS`·`NEWSLETTER`) 적어뒀었다. 실제 CSV를 직접 파싱해 확인한 결과 **11개**이며 `CANCEL`·`SHIPPING`·`SUBSCRIPTION`이 맞고, `contact_human_agent`/`contact_customer_service`는 별도 `CONTACT` 카테고리에 속한다(이전엔 "카테고리 없음"으로 잘못 기재됨). 엔티티 플레이스홀더도 데이터셋 카드 설명("약 30종")과 달리 **`instruction` 컬럼에는 9종만** 등장한다 — 나머지는 `response` 컬럼(정답셋으로 안 씀, 4.2절)에만 있다.

**27개 인텐트 × 카테고리 (실측)**

| 카테고리 | 인텐트 | 건수 |
|---|---|---|
| ACCOUNT (5,986) | `create_account` `delete_account` `edit_account` `switch_account` `recover_password` `registration_problems` | 각 ~995~1,000 |
| CANCEL (950) | `check_cancellation_fee` | 950 |
| CONTACT (1,999) | `contact_human_agent` `contact_customer_service` | 999 / 1,000 |
| DELIVERY (1,994) | `delivery_options` `delivery_period` | 995 / 999 |
| FEEDBACK (1,997) | `complaint` `review` | 1,000 / 997 |
| INVOICE (1,999) | `check_invoice` `get_invoice` | 1,000 / 999 |
| ORDER (3,988) | `cancel_order` `change_order` `place_order` `track_order` | 998 / 997 / 998 / 995 |
| PAYMENT (1,998) | `check_payment_methods` `payment_issue` | 999 / 999 |
| REFUND (2,992) | `check_refund_policy` `get_refund` `track_refund` | 997 / 997 / 998 |
| SHIPPING (1,970) | `change_shipping_address` `set_up_shipping_address` | 973 / 997 |
| SUBSCRIPTION (999) | `newsletter_subscription` | 999 |

**엔티티 플레이스홀더 9종 (`instruction` 컬럼, 실측 빈도)**

| 플레이스홀더 | 빈도 | 등장 인텐트 | 처리 방식 |
|---|---|---|---|
| `{{Order Number}}` | 2,907 | cancel_order·change_order·track_order | shop.db 주문 (10%는 존재하지 않는 값) |
| `{{Account Type}}` | 1,011 | create_account·delete_account·edit_account·switch_account | 고정 어휘 목록에서 샘플링 (DB 무관) |
| `{{Person Name}}` | 887 | check_invoice·get_invoice | 연결된 고객의 `name` |
| `{{Account Category}}` | 822 | create_account·delete_account·edit_account·switch_account | 고정 어휘 목록에서 샘플링 (DB 무관) |
| `{{Refund Amount}}` | 624 | get_refund·track_refund | 연결된 주문의 `amount` |
| `{{Currency Symbol}}` | 372 | get_refund·track_refund | 연결된 주문의 `currency` → 기호 매핑 |
| `{{Delivery City}}` | 234 | delivery_options | 고정 도시 목록에서 샘플링 (주문 무관) |
| `{{Delivery Country}}` | 177 | delivery_options | 고정 국가 목록에서 샘플링 (주문 무관) |
| `{{Invoice Number}}` | 8 | check_invoice·get_invoice | 연결된 주문에서 파생(`INV-{주문 접미사}`) |

> `Account Type`/`Account Category`는 계정 생성 시나리오의 서술적 수식어(예: "business account", "premium account")라 우리 고객 등급(tier: standard/plus/vim)과 무관하다. `Delivery City`/`Delivery Country`는 "이 도시로 배송되나요?" 류의 가상 질의라 실제 주문과 연결할 필요가 없다(라우팅 표에서도 `delivery_options`는 `lookup_order`가 불필요).
>
> `change_shipping_address`·`check_cancellation_fee`·`payment_issue`·`check_invoice`·`get_invoice`는 텍스트에 `{{Order Number}}`가 없어도 라우팅 표(3.2절)상 `lookup_order`가 **필수**다 — 상담원이 고객 컨텍스트로 최근 주문을 조회하는 실제 CS 동작과 같다. 하이드레이션 시 이 인텐트들도 `order_id`를 배정한다(본문 텍스트 치환과는 별개로 메타데이터 필드로).

**`flags` 코드** — 언어 생성 태그. 파이프라인이 실제로 사용한다.

| 그룹 | 코드 |
|---|---|
| 어휘 | `M` 형태 변화 · `L` 동의어 |
| 구문 | `B` 기본 · `I` 의문 · `C` 등위 · `N` 부정 |
| 레지스터 | `P` 정중 · `Q` 구어 · **`W` 공격적 표현** |
| 문체 | `K` 키워드 · `E` 축약 · `Z` 오타 |

- **`W`는 에스컬레이션 조건 E4**로 직접 쓴다
- `P`/`Q`/`Z`는 톤·강건성 평가의 **층화 샘플링 축**으로 쓴다(정중한 티켓만으로 평가하면 톤 점수가 낙관적으로 나온다)

### 4.2 ⚠️ `response` 컬럼을 정답셋으로 쓰지 않는다

Bitext의 `response`는 **플레이스홀더가 박힌 범용 템플릿**이며, **우리가 합성할 정책 문서에 근거하지 않는다.** 이걸 초안 품질의 ground truth로 쓰면 "우리 정책에 맞는 답"이 아니라 "Bitext 템플릿과 비슷한 답"을 평가하게 된다.

| 용도 | 허용 |
|---|---|
| 영어 CS 문체 참고(few-shot 예시 후보) | ✅ |
| 인텐트별 응답 구조 파악 | ✅ |
| **초안 품질/정책 준수 정답셋** | ❌ **금지** |
| **RAG 코퍼스 적재** | ❌ **금지** |

초안 품질은 우리가 만든 정책 문서 기준으로 **Judge + 사람 라벨**이 평가한다(6절).

### 4.3 티켓 하이드레이션 (플레이스홀더 → 실제 값)

Bitext의 `{{Order Number}}`는 **문자열 리터럴**이지 실제 값이 아니다. 그대로 쓰면 `lookup_order`가 조회할 대상이 없다. 따라서 **합성 DB를 먼저 만들고, 그 값을 플레이스홀더에 주입**한다.

```
1. scripts/build_synthetic_data.py  →  shop.db (주문·고객) + 정책 문서
2. scripts/hydrate_tickets.py       →  Bitext instruction의 {{...}} 를
                                        shop.db 의 실제 레코드로 치환
                                        (시드 고정, 티켓↔주문 매핑 기록)
3. 결과: data/synthetic/tickets.jsonl
         {ticket_id, text, intent, category, flags, customer_id, order_id, order_exists}
```

- **의도적으로 일부는 존재하지 않는 주문번호로 채운다** → 에스컬레이션 E6 경로를 실제로 발생시켜야 테스트가 된다(초깃값 10%)
- `order_exists`(bool|null)가 하이드레이션 매핑 자체다 — `order_id`가 실제 `shop.db`에 있는지(`true`), 의도적으로 존재하지 않게 채운 것인지(`false`), 애초에 주문이 필요 없는 인텐트인지(`null`)를 기록해 골든셋(특히 `escalation_golden`의 E6 케이스) 정답 산출에 그대로 쓴다
- `order_id`가 필요한 인텐트는 라우팅 표(3.2절)에서 `lookup_order`가 "필수"인 10종(`cancel_order` `change_order` `track_order` `check_cancellation_fee` `get_refund` `track_refund` `change_shipping_address` `payment_issue` `check_invoice` `get_invoice`) — 티켓 텍스트에 `{{Order Number}}`가 없어도 이 인텐트면 `order_id`를 배정한다(4.1절 참고)
- **실측 검증 완료(2026-07-28)**: 26,872건 전체 하이드레이션, 잔여 미치환 플레이스홀더 0건, `order_exists=true` 건은 전부 `shop.db` 실조회 성공·소유 고객 일치, `order_exists=false` 건은 전부 실조회 실패(0건 충돌) 확인. `order_id` 배정 비율 중 fake 비중 약 9.5%(인텐트별 8.4~10.7%, 목표 10%에 근접)

### 4.4 합성 데이터

| 항목 | 내용 |
|---|---|
| 정책 문서 | 가상 이커머스사(가칭 `Northwind Retail`) 영문 규정 — 반품·교환/환불/배송/취소수수료/보증/결제수단/멤버십 등급 7종 문서. **각 조항에 번호를 부여**(`RET-03`, `SHIP-07` …)해 인용 가능하게 |
| 주문 DB | `orders(order_id, customer_id, status, carrier, tracking_no, ordered_at, delivered_at, amount, currency)` |
| 고객 DB | `customers(customer_id, name, tier, joined_at, country)` — tier: `standard` / `plus` / `vip`. **`name`은 4.1절 `{{Person Name}}` 하이드레이션에 필요해 스키마에 추가** |
| 재현성 | 시드 고정. `scripts/build_synthetic_data.py`로 언제든 재생성 |

**정책 문서는 tier·기한 분기를 반드시 포함한다.** 그래야 `check_customer_tier`가 장식이 아니라 실제로 답을 바꾸는 도구가 된다(예: `RET-03` 반품 기한 standard 14일 / plus 30일 / vip 60일).

### 4.5 데이터 취급 규칙

- **`data/raw/`는 커밋하지 않는다.** `.gitignore`로 제외하고 `scripts/download_bitext.py`로 재현한다. CDLA-Sharing-1.0은 재배포 시 동일 라이선스·출처 표기를 요구하므로, 레포에 원본을 담지 않는 편이 단순하다
- README와 `data/README.md`에 **출처·라이선스를 명시**한다
- **⛔ 절대 금지**: 실제 고객 문의, 실제 주문 정보, 식별 가능한 개인정보 수집

**보호 경로** (사람 승인 없이 에이전트가 수정 불가 — `.claude/hooks/`가 강제):
`data/raw/` · `evals/golden/` · `evals/runners/` · `.env`

---

## 5. PII 마스킹 정책

CS 도메인에는 분필에 없던 문제가 있다: **모든 식별자를 마스킹하면 도구가 동작하지 않는다.**

| 구분 | 대상 | 처리 |
|---|---|---|
| **마스킹** (개인 식별정보) | 이메일 · 전화번호 · 신용카드번호 · 우편주소 · 인명 | `{{EMAIL}}` `{{PHONE}}` `{{CARD}}` `{{ADDRESS}}` `{{NAME}}` 토큰으로 치환 |
| **유지** (내부 식별자) | 주문번호 · 송장번호 · 고객ID | 그대로 — `lookup_order`가 입력으로 써야 함 |

- 마스킹은 **모델 호출 이전**에 수행한다. 순서를 바꾸지 않는다
- 초안에 마스킹 토큰이 남는 것은 **정상**이다. 상담원이 검토 단계에서 복원한다("상담원 최종 책임" 원칙과 일관)
- `save_draft` 게이트 ①이 검사하는 것은 **마스킹되지 않은 원본 PII 패턴**의 출현이다
- 신용카드번호는 Luhn 검증을 통과하는 패턴만 카드로 판정한다(오탐 억제)

---

## 6. 평가 설계

**원칙**: ① LLM Judge는 사람 라벨과 먼저 일치율을 검증한다 ② 정량(함수)/정성(Judge·사람)을 분리한다 ③ 골든셋은 코드에 하드코딩하지 않고 `evals/golden/*.jsonl`로 외부화한다.

### 6.1 모듈 ① Triage

| 지표 | 판정 | 통과 기준(시작값) |
|---|---|---|
| 인텐트 정확도 | 함수 (Bitext 라벨) | ≥ 0.85 |
| 인텐트 macro-F1 | 함수 | ≥ 0.80 |
| 카테고리 정확도 | 함수 | ≥ 0.92 |
| confidence 캘리브레이션 | 함수 | 오분류 건의 confidence 분포가 정분류보다 유의하게 낮은가 |

> **macro-F1을 함께 보는 이유**: Bitext는 인텐트당 약 1,000건으로 **분포가 균등**하므로 불균형 보정 목적은 아니다. 목적은 **특정 인텐트의 국소적 붕괴 탐지**다 — 의미가 인접한 쌍(`check_invoice`↔`get_invoice`, `check_refund_policy`↔`get_refund`, `change_shipping_address`↔`set_up_shipping_address`)에서 한쪽이 무너져도 전체 accuracy는 거의 안 움직인다. **혼동행렬을 함께 리포트해 인접 쌍 혼동을 별도로 확인한다.**

### 6.2 모듈 ② Reply Agent

| 우선 | 지표 | 판정 | 통과 기준(시작값) |
|---|---|---|---|
| 🔴 | PII 마스킹 누락률(FN) | 함수 | **0** |
| 🔴 | 정책 위반 검출 Recall | 함수(골든셋) | ≥ 0.95 |
| 🔴 | 정책 위반 검출 F1 | 함수 | 참고값 |
| 🔴 | 근거 없는 확약률 | 함수(게이트 로그) | **0** |
| 🟡 | 톤 적절성 | LLM Judge | 5점 평균 ≥ 4.0 |
| 🟡 | **Judge 신뢰도 (Cohen's κ)** | 사람 라벨 대비 | ≥ 0.4 |
| 🟢 | 정책 RAG Recall@5 / MRR | 함수 | R@5 ≥ 0.8 |
| 🟢 | 과정: 평균 반복수·도구 호출 수·latency | 함수 | 예산 내 수렴 |
| ⭐ | **에스컬레이션 Recall** | 함수(골든셋) | ≥ 0.9 |
| ⭐ | 에스컬레이션 FP율 | 함수 | 참고값 — 트레이드오프는 사람 판단 |
| 🏁 | 상담원 무수정 채택률 | 사람 | 북극성 |
| ⚪ | 라이브 공지 반영 FP/FN, 게이트⑥ 발동 건수(Phase 12a) | 함수(골든셋) | **첫 사이클은 리포트만** — `check_thresholds.py`에 아직 게이트로 넣지 않는다(2~3회 이력 후 사람이 결정) |

**에스컬레이션 Recall이 이 프로젝트 고유 지표다.** "사람이 개입해야 했던 케이스를 실제로 넘겼는가". FN(넘겼어야 하는데 자동 초안을 낸 것)이 FP보다 훨씬 위험하므로 Recall만 게이트로 두고 FP율은 참고값으로 관리한다.

**Judge 신뢰도를 먼저 검증한다.** κ가 목표 미달이면 그 Judge 점수로 통과/재시도를 결정하지 않는다. 못 믿을 Judge 위에 다른 수치를 쌓으면 전부 다시 해야 한다.

### 6.3 골든셋 7종

| 파일 | 규모 | 라벨 | 만드는 법 |
|---|---|---|---|
| `triage_golden.jsonl` | 200 | intent · category | Bitext에서 층화 샘플링(인텐트당 약 7–8건, `flags` 다양성 확보) |
| `pii_golden.jsonl` | 50 | PII 스팬 위치·유형 | 하이드레이션된 티켓에 **영문 PII를 의도적으로 주입**하고 정답 스팬 기록 |
| `policy_violation_golden.jsonl` | 50 | 위반 유형·스팬 | 초안에 위반을 의도적으로 심음(무근거 확약 20 / 정책 모순 15 / 인용 누락 10 / 범위 밖 약속 5) |
| `tone_golden.jsonl` | 30 | 5점 척도(사람) | Phase 6 완료 후 생성된 실제 초안에 직접 라벨링 |
| `escalation_golden.jsonl` | 40 | `should_escalate` + 해당 조건 ID | E1–E9 각 조건을 재현하는 케이스 + 에스컬레이션 불필요한 대조군 |
| `retrieval_golden.jsonl` | 30 | 질의 → 정답 조항 번호 | 합성 정책 문서에서 직접 작성 |
| `notices_golden.jsonl` | 19 | 인지 대상 공지(`expected_grounded_ids`) · 에스컬레이션(E9) | Phase 12a. 활성+scope 일치(반영 필요) / 활성+scope 불일치(반영 금지) / 비활성(만료·`active=false`·TTL 초과) / 조회 실패(필수 인텐트→E9). 전부 결정론적(`is_notice_active` 순수 함수 + stub 소스) — best-effort 구간 없음. 각 행이 자체 공지 레코드와 고정 `as_of` 기준일을 포함해 실행 시점과 무관하게 재현된다 |

> `pii_golden`이 필요한 이유: Bitext는 이미 익명화돼 있어 **마스킹할 실제 PII가 없다.** 🔴 지표를 측정하려면 주입한 테스트셋이 반드시 있어야 한다.

**결정론적 테스트 목록 — 라이브 공지 활성 판정(Phase 12a)**: `is_notice_active()`의 경계값은
전부 `tests/test_notices.py`에 결정론적 단위 테스트로 고정돼 있다 — 시작일 당일(포함) · 종료일
당일(포함) · 종료일 다음날(제외) · `valid_until` 공란 + 기본 TTL 만료일(포함) · TTL 만료 다음날
(제외) · `active=false`가 날짜 범위와 무관하게 항상 우선.

> ⚠️ **분필에서 얻은 함정**: "eval이 존재하는가"와 "내 변경이 eval이 실제로 exercise하는 경로에 있는가"는 별개다. 분필은 생성 프롬프트를 개선했는데 eval 수치가 전혀 변하지 않았고, 원인은 eval이 하드코딩된 고정 출력을 채점하는 구조였기 때문이다. **CS eval은 실제로 파이프라인을 돌려 새 초안을 생성한 뒤 채점하도록 설계한다.**

### 6.4 실행 비용

**개발 루프 = 스모크셋(20건), 전체셋 = 사람이 직접 실행.** `--full`은 훅으로 차단한다.

생성 모델 `claude-sonnet-5` 기준($3 / $15 per MTok) 개략 추정:

| 항목 | 토큰(입력/출력) | 건당 | 비고 |
|---|---|---|---|
| Triage 1건 | ~1K / ~0.2K | ≈ $0.006 | |
| 초안 1건(재시도 없음) | ~8K / ~1.5K | ≈ $0.047 | 멀티턴 누적 포함 |
| 초안 1건(평균 재시도 반영) | ×1.5 | ≈ $0.070 | |
| Judge 1건 | ~3K / ~0.5K | **요금 확인 후 갱신** | 벤더 요금 미확정 |

| 실행 단위 | 개략 비용 |
|---|---|
| 스모크셋(초안 20건) | ≈ $1.4 + Judge |
| 전체 reply eval(50건) | ≈ $3.5 + Judge |
| 전체 triage eval(200건) | ≈ $1.2 |
| **전체 1회 합계** | **≈ $5 내외 + Judge** |

→ **billing alarm은 이 수치를 기준으로 건다.** 프롬프트 캐싱을 적용하면 반복 실행의 입력 비용이 크게 줄어든다(시스템 프롬프트·정책 청크가 요청 간 동일) — 최적화 항목으로 남겨둔다.

---

## 7. API 계약

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| `GET` | `/health` | — | `{"status": "ok"}` (인증 불필요) |
| `POST` | `/triage` | `{ticket_text, flags?}` | `{intent, category, confidence, requires_human, reason}` |
| `POST` | `/reply` | `{ticket_text, customer_id?, flags?, ticket_ref?}` | `{outcome, draft?, cited_policies?, tools_used?, escalation_reason?, judge_result?}` |
| `POST` | `/reply/stream` | 동일 | SSE — 진행 이벤트 스트리밍 |

- `/health` 외 전 엔드포인트는 `X-API-Key: $CS_API_KEY` 서버 간 인증 요구
- SSE 이벤트: `{"status": "progress"|"done"|"error", ...}`. 내부 예외 상세는 노출하지 않는다.
  `progress` 단계는 `stage: "triage"|"plan"|"agent"|"judge"|"validate"|"notify"` — `notify`는
  에스컬레이션이 확정됐을 때만 `done` 직전에 한 번 나온다(Phase 11)
- 동시 요청 제한: `asyncio.Semaphore(2)` — 획득 실패 시 429 (분필과 동일)
- `ticket_ref`(선택, 불투명 문자열): 호출한 외부 CS 시스템의 티켓 ID/URL. 이 서비스는
  티켓을 영구 저장하지 않으므로(9절 하드룰) 내부 `REQ-` ID로는 상담원이 원본으로 돌아갈
  수 없다 — 에스컬레이션 알림에 그대로 되돌려주는 용도로만 쓰고 해석하지 않는다
  (Phase 11, `MCP_INTEGRATION.md` 5절)

---

## 8. 기술 스택

| 구분 | 선택 |
|---|---|
| 백엔드 | FastAPI (비동기) |
| 오케스트레이션 | LangGraph (reply agent) / 단일 호출 (triage) |
| 벡터스토어 | ChromaDB + Rerank (BGE-reranker) |
| 임베딩 | BGE-M3 (CPU) |
| 생성 LLM | **Anthropic `claude-sonnet-5`** (기본, `ChatAnthropic`) |
| Judge LLM | **OpenAI `gpt-5.6-luna`** — 생성과 **다른 벤더**. 선행 프로젝트 채택값이며 **본 프로젝트에서 κ 재검증 후 확정** |
| 커스텀 어댑터 | Ollama / vLLM 자체 호스팅 / RunPod 서버리스 (`BaseChatModel` 직접 상속) |
| 합성 데이터 DB | SQLite |
| 프론트엔드 | Next.js (상담원 검토 UI) |
| 트레이싱·eval | LangSmith / Ragas |
| 배포 | Docker + Caddy HTTPS (클라우드 VM) |

**모델 선정 근거 (초기값, 측정 후 갱신)**

- 생성 = `claude-sonnet-5`: 이 프로젝트는 확신도 낮은 케이스를 에스컬레이션(E1, E7/E8)으로 걸러내는 안전망이 이미 있어, 생성 모델이 모든 edge case를 완벽히 처리할 필요는 없다 — ReAct 툴콜링·정책 인용·톤 유지 수준에서는 `claude-opus-5`가 오버스펙이라고 판단, 비용 대비 적절한 `claude-sonnet-5`를 기본값으로 채택(2026-07-29). 품질 회귀가 실제로 관측되면 `claude-opus-5`로 올리고 eval로 확인한다 — 올리는 판단도 **측정 후에** 한다. 실측 A/B는 실제 Anthropic API 키가 준비된 뒤(현재 개발은 Ollama로 진행 중) 진행 예정
- Judge = 크로스 벤더: "생성 모델이 자기 글을 자기가 채점하지 않는다"의 가장 강한 형태
- 모델 비교·확정 데이터는 `MODEL_SELECTION.md`에 누적

> 모델 ID는 정확한 문자열로만 쓴다. 날짜 접미사를 임의로 붙이지 않는다.

### 환경변수

| 변수 | 설명 | 기본값 |
|---|---|---|
| `CS_API_KEY` | Next.js → FastAPI 서버 간 인증 (양쪽 동일한 긴 무작위 값) | 필수 |
| `LLM_BACKEND` | 생성 백엔드 — `anthropic` / `openai` / `ollama` / `runpod` | `anthropic` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | 생성 모델 | — / `claude-sonnet-5` |
| `JUDGE_BACKEND` | Judge 백엔드 — 생성과 **독립 전환**. 키 없거나 실패 시 fail-fast | `openai` |
| `OPENAI_API_KEY` / `OPENAI_JUDGE_MODEL` | Judge 모델 | — / `gpt-5.6-luna` |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | 로컬 개발 백엔드 | `http://localhost:11434` / — |
| `RUNPOD_API_KEY` / `RUNPOD_ENDPOINT_ID` | 커스텀 어댑터 경로 (Phase 8) | — |
| `CHROMA_PERSIST_DIR` | ChromaDB 저장 경로 | `./chroma_db` |
| `BGE_EMBED_MODEL` / `BGE_RERANK_MODEL` | 임베딩·리랭킹 | `BAAI/bge-m3` / `BAAI/bge-reranker-base` |
| `SHOP_DB_PATH` | 합성 주문·고객 DB | `./data/synthetic/shop.db` |
| `TRIAGE_CONFIDENCE_THRESHOLD` | 에스컬레이션 E1 임계값 | `0.70` |
| `REPLY_BUDGET` / `REPLY_TURN_CAP` | 재시도·턴 상한 | `2` / `12` |
| `MALFORMED_TOOL_CALL_STREAK` | tool_call 형식 오류 연속 허용 횟수 | `3` |
| `SAVE_DRAFT_FAIL_STREAK` | 에스컬레이션 E7 임계값 | `3` |
| `JUDGE_PASS_POLICY` / `JUDGE_PASS_TONE` | validate 통과 임계값(1-5 척도) | `4` / `4` |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` | LangSmith (기본 비활성, 옵트인) | `false` / — / `cs-assistant` |
| `MCP_NOTIFIER` | 에스컬레이션 알림 백엔드(Phase 11) — `noop`/`slack`. 미설정 시 조용히 비활성(fail-soft) | `noop` |
| `SLACK_MCP_URL` / `SLACK_MCP_TOKEN` | Slack MCP 서버 엔드포인트(`/mcp` 경로 포함) · 앞단 인증 토큰 | — |
| `SLACK_ESCALATION_CHANNEL` | 알림 보낼 Slack 채널 ID(`C…`) | — |
| `SLACK_MCP_TOOL_NAME` | 도구 자동 발견이 틀렸을 때만 지정하는 탈출구 | — |
| `MCP_NOTIFY_TIMEOUT` | 알림 호출 타임아웃(초) | `5` |
| `SLACK_BOT_TOKEN` / `SLACK_TEAM_ID` | `xoxb-…` 봇 토큰·워크스페이스 ID — **MCP 서버 컨테이너**가 씀(우리 앱은 안 읽음) | — |
| `NOTICE_SOURCE` | 라이브 공지 소스(Phase 12) — `noop`/`stub`/`notion`. 미설정(noop)은 기능 비활성이며 **E9가 아니다** | `noop` |
| `NOTICE_DEFAULT_TTL_DAYS` | `valid_until` 공란 공지의 기본 유효기간(일) | `14` |
| `NOTICE_MAX_COUNT` / `NOTICE_MAX_BODY_CHARS` | `check_live_notices` 반환 건수·본문 길이 상한(컨텍스트 폭주 방지) | `5` / `500` |
| `NOTION_MCP_URL` / `NOTION_MCP_TOKEN` | 노션 MCP 서버 엔드포인트(`/mcp` 포함) · 앞단 인증 토큰(Phase 12c) | — |
| `NOTICE_DB_ID` | 노션 공지 **데이터베이스 ID**(URL의 `?v=` 뒤 뷰 ID가 아니다 — 실측 중 실제로 혼동 발생) | — |
| `NOTICE_MCP_TIMEOUT` | 공지 조회 타임아웃(초). 루프 안 도구라 턴 예산에 직접 영향 | `8` |
| `NOTION_DB_TOOL_NAME` / `NOTION_QUERY_TOOL_NAME` | 도구 자동 발견이 틀렸을 때만 지정하는 탈출구 | — |
| `NOTION_TOKEN` | 노션 통합 토큰 — **MCP 서버 컨테이너**가 씀(우리 앱은 안 읽음) | — |

---

## 9. 보안 · 개인정보

- **PII 마스킹은 입력 단계에서, 외부/모델 호출 이전에** 수행한다(5절). 순서를 바꾸지 않는다
- 실제 고객 데이터 미사용 — 공개 데이터셋 + 합성
- 사용자 입력(티켓 본문)은 **비저장**. 영구 저장은 공개/합성 코퍼스뿐
- **로그·캐시에 PII 금지**
- 시크릿은 `.env`(gitignore). `.env.example`만 커밋. 코드 하드코딩 금지
- 초안 출력에 **"상담원 최종 책임(보조수단)" 고지** 표시
- 감사 로그: 티켓별 도구 호출·인용 정책·outcome 기록(PII 제외)
- LangSmith 트레이싱은 **마스킹 이후** 단계만. 기본값 비활성, 옵트인

> ⚠️ **외부 전송 트레이드오프 (명시적으로 수용)**: `JUDGE_BACKEND=openai`(기본값)에서는 초안 생성마다 티켓 본문과 초안이 OpenAI로 전송된다. PII 마스킹은 이 호출 이전에 이미 적용돼 있다. 전부 로컬로 처리하려면 `JUDGE_BACKEND=local`로 전환한다 — 단 그 경우 Judge 신뢰도가 재검증 대상이 된다.

---

## 10. 벤더 연동 전략 — 정식 지원 vs 커스텀 어댑터

이 프로젝트의 두 번째 축이다. **두 경로를 모두 구현하고, 판단 기준을 문서로 남긴다.**

### 판단 기준

| 기준 | 정식 지원으로 충분 | 커스텀 어댑터 필요 |
|---|---|---|
| LangChain 공식 패키지 존재 | 있음 (`langchain-anthropic`, `langchain-openai`, `langchain-aws`) | 없거나 표준과 다름 |
| API 계약 | 단일 동기 요청-응답 | **비동기 job queue**(제출 → job id → 상태 폴링) |
| 예시 | Anthropic API, OpenAI, Bedrock, SageMaker Serverless | RunPod serverless (자체 job queue) |

**근거**: 분필의 `chat_runpod.py`를 검토한 결과, RunPod serverless는 비동기 job queue 프로토콜을 쓰기 때문에 `BaseChatModel`을 직접 상속하는 커스텀 어댑터가 **실제로 필요했던** 케이스였다. GPU 서버리스 특성(긴 콜드스타트, 워커 오토스케일링 큐잉)상 구조적으로 비동기가 자연스럽다. 반면 Bedrock(`InvokeModel`/`Converse`)과 SageMaker Serverless Inference는 동기 요청-응답이라 `langchain-aws`의 공식 클래스로 충분하다.

### 구현 경로

```
app/common/llm/
├── base.py                 # LLMBackend ABC (generate) — Judge 등 비-LangChain 경로
├── factory.py              # get_llm_backend() / get_judge_backend() — env로 전환
└── backends/
    ├── anthropic.py        # 정식 경로: ChatAnthropic (기본)
    ├── openai.py           # 정식 경로: ChatOpenAI (Judge 기본)
    ├── ollama.py           # 로컬 개발
    └── chat_runpod.py      # ★ 커스텀 어댑터: BaseChatModel 직접 상속
```

**커스텀 어댑터가 실제로 구현해야 하는 것** (분필에서 확인된 목록):
- `_generate` / `_agenerate` — 동기·비동기 진입점
- `bind_tools` — LangChain 도구를 OpenAI 호환 tool schema로 변환(`convert_to_openai_tool`)
- 메시지 변환 양방향 — LangChain `BaseMessage` ↔ 벤더 포맷 (role 매핑, `tool_calls` 구조 변환, `tool_call_id` 처리)
- 폴링 루프 — `/run` 제출 후 **동일한 job_id를 폴링**. 제출 응답을 못 받았다고 무작정 재제출하면 중복 실행이 된다

**산출물**: `VENDOR_INTEGRATION.md`

### 파이프라인 규칙

파이프라인 코드에서 `ChatAnthropic`을 **직접 import 하지 않는다.** 항상 `app/common/llm/` 인터페이스를 경유한다. 이 규칙이 깨지면 벤더 전환 실험 자체가 불가능해진다.

### MCP 연동 — 세 번째 벤더 판단 사례 (Phase 11·12)

MCP는 **표준 프로토콜이 있고 공식 SDK도 있는** 경우다 — 위 판단 기준표에서 "정식
지원으로 충분" 쪽에 가깝지만, RunPod과 달리 표준화된 프로토콜 자체(공식 SDK)를 직접
다루는 게 정식 경로라는 점이 다르다. 벤더 고정(RunPod처럼 한 곳에 맞춰 어댑터를 짬)과
프로토콜 발견(MCP처럼 런타임에 상대를 알아내야 함) 둘 다 이 프로젝트에서 실제로
다뤄본 셈이다.

**두 번 붙였고, 성격이 정반대라 배치도 정반대가 됐다.**

| | Slack 알림 (Phase 11) | Notion 공지 (Phase 12) |
|---|---|---|
| 성격 | **쓰기 · 부작용 있음** | **읽기 · 멱등** |
| 배치 | 루프 **밖** — `app/main.py` 서비스 계층 | 루프 **안** — `reply/tools.py` 9번째 도구 |
| 호출 주체 | **코드**(결정론적, 에스컬레이션 확정 4지점) | **에이전트**(자율 판단) |
| 실패 계약 | **fail-soft** — 알림 실패가 판정을 뒤집으면 안 된다 | **fail-fast** — 조회 실패를 빈 결과로 삼키면 조용히 틀린 답이 나간다 |
| 실패의 결과 | 로그만 남고 계속 | 필수 인텐트면 **E9 에스컬레이션** |

원래 규칙은 "MCP 호출은 무조건 `reply/tools.py` 밖"이었는데, 그 근거(재시도 루프 안
중복 발송 위험)는 **쓰기에만** 적용된다. 읽기 전용 도구가 등장하며 규칙을 **부작용
유무 기준으로 다시 갈랐다**(2026-07-30, `CLAUDE.md` 개정).

**도구 발견은 두 서버에서 난이도가 달랐다.** Slack은 도구 8개라 스키마+이름 힌트로
충분했지만, Notion은 **24개**를 노출하고 그중 `API-update-a-data-source`가 필수 인자
(`data_source_id`)가 조회 도구와 동일해 **스키마 필터를 그대로 통과한다** — "이름을
하드코딩하지 않는다"가 그 자체로 안전을 보장하지 않아 **쓰기 동사 배제 단계**를 따로
넣었다.

설계·실측 검증 전체는 `MCP_INTEGRATION.md` 참고 — 두 서버 모두 컨테이너를 실제로 띄워
`initialize → tools/list → tools/call` 전 구간을 확인했고, Slack은 실제 발송
(2026-07-29), Notion은 실제 DB 조회 + 공지 반영 before/after까지 검증했다(2026-07-31).

---

## 11. 개발 하네스 · 루프 (요약)

전체 설계는 `CS_CLAUDE_CODE_HARNESS_LOOP.md`.

**핵심 원칙**: eval의 정답(ground truth)과 eval 실행 코드는 **에이전트가 수정할 수 없어야 한다.**

| 층 | 수단 | 강제 대상 |
|---|---|---|
| Layer 1 | `CLAUDE.md` | 판단 기준 (모델의 협조에 의존) |
| Layer 2 | `.claude/settings.json` `permissions.deny` | 선언적 1차 방어 |
| Layer 3 | `.claude/hooks/*.py` (PreToolUse) | 우회 경로까지 차단하는 2차 방어 (exit 2) |

훅은 **Python(stdlib `json`)으로 작성한다** — jq 외부 의존성 없이 이식 가능하고, 프로젝트 언어와 일치해 테스트를 붙이기 쉽다.

**차단 대상**: 보호 경로 쓰기 / API 키 패턴 / `.env` 열람·전송 / `--full` eval 실행.

**루프 3종**

| 루프 | 종료 조건 | max_attempts | 에스컬레이션 |
|---|---|---|---|
| A. 기능 구현 | pytest 통과 | 3 | 세 번의 가정과 오류를 요약 후 중단 |
| B. RAG 품질 | Recall@5 ≥ 목표 | 2 | 청킹 전략 선택지 제시 후 사람 결정 |
| C. 가드레일 | 위반 F1 ≥ 목표 & 톤 ≥ 임계 | 2 | **반드시 사람 리뷰** |

루프 C는 **자동 종료를 만들지 않는다.** 정책 위반 검출의 FP/FN 균형은 제품 판단이지 기술 판단이 아니다.

> 종료 조건보다 **포기 조건**이 설계하기 어렵고 더 중요하다. 3회 실패 시 멈추고 "무엇을 가정했고 무엇이 틀렸는지"를 보고하게 한다.

**대칭성이 이 프로젝트의 서사다**: 에이전트 제품에 가드레일을 넣는 것과, 에이전트로 개발하는 과정에 가드레일을 넣는 것은 같은 문제다.

---

## 12. 레포 구조

```
cs-assistant/
├── CLAUDE.md · DESIGN.md · PROMPTS.md · README.md
├── EVAL.md · VENDOR_INTEGRATION.md · HARNESS_ENGINEERING.md · MODEL_SELECTION.md
├── .claude/
│   ├── settings.json               # 공유 훅/권한 (커밋)
│   ├── settings.local.json         # 개인 오버라이드 (gitignore)
│   ├── hooks/                      # block_protected_paths.py 등 (Python)
│   ├── rules/                      # eval-integrity.md, dev-loop.md, prompt-change-policy.md
│   ├── agents/                     # eval-reviewer.md, prompt-critic.md
│   └── agent-memory/dev/MEMORY.md  # 반복 실패 패턴 (gitignore)
├── app/
│   ├── main.py                     # FastAPI (7절 API 계약)
│   ├── common/
│   │   ├── llm/                    # 벤더 추상화 (10절)
│   │   ├── mcp/                    # MCP 클라이언트 (10절)
│   │   │   ├── base.py·client.py·factory.py·toolschema.py
│   │   │   ├── backends/           # noop / slack — 알림(쓰기·루프 밖·fail-soft)
│   │   │   └── notices/            # 라이브 공지 조회(읽기·루프 안·fail-fast)
│   │   │       ├── base.py·activity.py·factory.py
│   │   │       └── backends/       # noop / stub / notion
│   │   ├── rag/                    # 파싱·청킹·임베딩·리랭킹·ChromaDB
│   │   └── privacy.py              # PII 마스킹 (5절)
│   └── modules/
│       ├── triage/                 # classifier.py
│       └── reply/                  # graph.py / tools.py / judge.py / state.py / routing.py
├── prompts/                        # ★ 프롬프트·루브릭 = 버전 관리 대상, 단독 커밋
├── evals/
│   ├── golden/                     # ★ 보호 경로 — 골든셋 7종 (6.3절)
│   ├── runners/                    # ★ 보호 경로 (check_thresholds.py 포함)
│   └── reports/                    # 실행 결과 (gitignore)
├── data/
│   ├── README.md                   # 출처·라이선스 명시
│   ├── raw/                        # ★ 보호 경로 + gitignore — Bitext 원본
│   └── synthetic/                  # 정책 문서·shop.db·tickets.jsonl (생성물)
├── frontend/                       # Next.js 상담원 검토 UI
├── tests/ · scripts/
└── .env.example
```

★ = 보호 경로

---

## 13. 배포

**구성: 단일 클라우드 VM + Docker + Caddy HTTPS.**

```
브라우저 ─→ Caddy(HTTPS) ─→ Next.js(3000) ─→ FastAPI(8000) ─→ Anthropic API
                                                  │              OpenAI API (judge)
                                                  ├─ ChromaDB (볼륨)
                                                  └─ SQLite (합성 주문/고객)
```

- 생성·Judge 모두 **관리형 API**라 GPU 서버리스가 필수 경로가 아니다 → 분필 대비 인프라가 단순하고 운영비가 낮다
- **RunPod 서버리스는 "커스텀 어댑터 경로 데모"용으로만** 붙인다(상시 운영 아님)
- 서버 간 인증: Next.js → FastAPI `CS_API_KEY`
- **billing alarm 필수** — 6.4절 비용 추정을 기준으로 설정
- 배포 사다리: Dockerfile → docker-compose(로컬 검증) → VM 배포 → Caddy HTTPS → GitHub Actions CI

**CI = 최종 게이트**

```yaml
# .github/workflows/ci.yml — 모델 호출 없는 경량 CI (매 PR 블로킹)
- pytest tests/ -q                     # 순수 로직 유닛테스트
- 백엔드 import 스모크 + 프론트 lint/build
```

전체 eval은 CI에서 자동으로 돌리지 않는다 — 생성·채점 결과의 변동성이 자동 블로킹 게이트에 적합하지 않고, 비용이 실제로 나간다. `evals/runners/check_thresholds.py`는 **사람이 실행**하고, 그 스크립트 자체도 보호 경로에 둔다(임계값을 낮춰 통과시키는 경로 차단).

**모델 호출 없이 단위 테스트 가능한 것** (CI가 실제로 지키는 범위):
`mask_pii` · `save_draft` 게이트 6종 · 에스컬레이션 조건 E1–E9 판정 · 인텐트→도구 매핑 · 공지 활성 판정 · `validate_node` threshold 로직 · 청킹 · 하이드레이션.
→ 안전에 직결되는 로직이 전부 결정론적 코드에 있어서 **모델 없이 CI로 지킬 수 있다.** 이건 우연이 아니라 "LLM이 판단하고 코드가 결정한다" 원칙의 부수 효과다.

---

## 14. 빌드 순서

`PROMPTS.md`에 Phase별 프롬프트와 완료 기준이 있다.

1. **하네스 먼저** — 훅·권한·차단 검증 (코드보다 먼저)
2. 스캐폴딩 → 데이터 준비(다운로드·합성·하이드레이션) → RAG → LLM 추상화(정식 경로)
3. Triage → Reply Agent(judge/validate/escalate)
4. **평가 체계** — Judge 신뢰도부터
5. 커스텀 어댑터 + `VENDOR_INTEGRATION.md`
6. UI → 배포 → CI → `HARNESS_ENGINEERING.md`

---

## 15. 결정 완료 / 미결정

**확정**
- 도메인: 이커머스 CS로 얇게 특화, verticalization 2단계 구조
- **파이프라인 언어: 영어 / 문서: 한국어** (0절)
- 데이터셋: Bitext(26,872 / 27 인텐트 / 10 카테고리 / 영어 / CDLA-Sharing-1.0) + 합성 정책·주문 DB
- **`response` 컬럼은 정답셋으로 쓰지 않음** (4.2절)
- 생성 백엔드 기본: Anthropic `claude-sonnet-5` (`ChatAnthropic`)
- Judge: 크로스 벤더 — κ 재검증 후 확정
- 훅 스크립트 언어: Python (stdlib json)
- 범위: 풀스택(Next.js UI) + 배포까지 1차 범위
- HITL: `escalated`를 1급 종료 상태로. 에스컬레이션 조건 E1–E9 (3.1절)
- 파라미터 초깃값 전체 (3.3절) — 측정 후 조정

**미결정 (개발 착수를 막지 않음)**
- 실사용자(테스트해줄 동료) 확보 여부 — 확보되면 "상담원 무수정 채택률" 실측 가능
- Judge 모델 최종 확정 — Phase 7의 κ 측정 결과에 따름. 벤더 요금도 이때 확인
- 루프 B/C의 최종 임계값 — 베이스라인 측정 후 확정
- `.claude/agent-memory/`를 커밋할지 (실패 패턴이 포트폴리오 자산이 될 수도 있음)
- 비용 상황에 따른 생성 모델 하향(`claude-sonnet-5`) 여부 — eval 회귀 확인 후 판단
- 프롬프트 캐싱 적용 (반복 eval 입력 비용 절감) — 최적화 단계
