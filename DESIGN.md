# CS 티켓 어시스턴트 — 설계 스펙

> 이커머스 CS 상담원을 보조하는 LLM 서비스. 포트폴리오 프로젝트.
> 목적: 분필(bunpil)에서 검증된 아키텍처(LangGraph ReAct + RAG + 가드레일 + eval)가
> **다른 도메인에도 일반화되는지 검증**하고, 그 위에 두 가지를 추가로 증명한다 —
> ① 벤더 통합 깊이(정식 경로 ↔ 커스텀 어댑터), ② 개발 과정 자체의 하네스/루프 설계.
>
> 기획 배경은 `CS_PROJECT_NOTES.md`, 개발 하네스는 `CS_CLAUDE_CODE_HARNESS_LOOP.md` 참고.
> 에이전트 행동 규칙은 `CLAUDE.md`, 빌드 절차는 `PROMPTS.md`.

---

## 1. 개요

- **목적**: 고객 문의 티켓을 받아 (1) 유형을 분류하고, (2) 사내 정책·주문 정보를 근거로 **답변 초안**을 생성해 상담원에게 제시
- **사용 맥락**: 포트폴리오(Agent·RAG·평가·벤더 통합·하네스 실습). 실사용자 확보 여부는 미정(6절 참고)
- **핵심 포지션**: **자동 응답이 아니라 상담원 증강(human-in-the-loop)**. 모든 출력은 상담원 검토 대상이며, 확신도가 낮으면 초안을 만들지 않고 사람에게 에스컬레이션한다
- **모듈**: ① 티켓 분류(Triage), ② 답변 초안 생성(ReAct Agent)
- **데이터 원칙**: 실제 고객 데이터 미사용. 공개 데이터셋(Bitext) + 합성 정책 문서 + 합성 주문 DB

### 도메인 범위 — "얇게" 특화한 이커머스

특정 버티컬(예: 패션 커머스)로 좁게 못 박지 않는다. 좁히면 (a) 도메인 실무 지식 부족으로 eval 신뢰도가 떨어지고, (b) 포트폴리오 평가 축이 "도메인 지식"으로 오해된다. 실제 축은 **ReAct + RAG + 가드레일 + eval 설계 역량**이다.

대신 tool·정책·eval을 **구체적으로 정의할 수 있을 만큼만** 좁힌다 → 이커머스가 적합(주문조회·반품/교환·배송 tool이 직관적이고, 정책 위반 정의가 명확하며, 본인이 검증 가능).

**verticalization을 구조로 차용**: Bitext 데이터셋의 27개 인텐트는 20개 버티컬에 공통인 것만 추출한 것이다. 이 2단계 설계를 그대로 프로젝트 구조로 가져온다.

1. 공통 인텐트로 CS 에이전트 **기본 동작** 구현 (`app/modules/triage`, `app/modules/reply` 코어)
2. 그 위에 **얇은 이커머스 특화 레이어**를 얹는다 (합성 정책 문서 + 도메인 tool + 도메인 eval)

→ 프레이밍: "이커머스 CS를 만들었다"가 아니라 "**범용 CS 파이프라인을 도메인에 특화시키는 과정을 재현했다**".

---

## 2. 아키텍처

### 전체 흐름

```
티켓 입력 → PII 마스킹 → [모듈 ①] Triage(인텐트·카테고리·confidence)
                              │
                    confidence < 임계 ─→ escalate(초안 없음, 사유만)
                              │
                              ▼
                   [모듈 ②] ReAct Agent ↔ tools(주문/정책/등급)
                              │ submit_for_review
                              ▼
                   judge_node (별도 백엔드: 정책 준수 + 톤 채점)
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
- **인텐트**: Bitext 27개 인텐트를 그대로 사용. 상위 카테고리 = 결제 / 기술문제 / 계정관리 (+ 이커머스 특화 레이어에서 주문·배송·반품 세분화)
- **`requires_human` 판정**: LLM이 `confidence`를 **기록**하고, 임계값 통과 여부는 **코드가 결정**한다 (설계 원칙 1)

### 모듈 ② 답변 초안 생성 — ReAct Agent (LangGraph)

분필 `exam` 모듈의 구조를 이식한다. 노드 순서: `plan → agent → judge → validate → (retry | escalate | end)`.

**도구(Tools)** — 모든 도구는 LLM 호출 없이 **순수 계산·검색·저장**만 수행한다. 추론과 문장 생성은 에이전트(LLM)가 직접 담당한다(도구 안에 LLM을 중첩하는 안티패턴 금지 — 분필에서 확립한 원칙).

| 도구 | 역할 | 구현 |
|---|---|---|
| `search_policy` | 반품·교환·환불·배송 정책 문서 검색 | ChromaDB + Rerank |
| `lookup_order` | 주문 상태·배송 추적 조회 | 합성 주문 DB(SQLite) |
| `check_customer_tier` | 고객 등급 조회(등급별 정책 분기용) | 합성 고객 DB |
| `validate_draft_format` | 초안 형식 검증(인사·본문·마무리, 길이) | 함수 |
| `save_draft` | 초안 저장 — 결정론적 게이트 통과 시에만 | 함수 |
| `discard_draft` | 초안 폐기(교체 시) | 함수 |
| `escalate_to_human` | 스스로 처리 불가 판단 시 명시적 에스컬레이션 신호 | 함수 |
| `submit_for_review` | 작성 완료 신호(인자 없음) | 함수 |

**`save_draft`의 결정론적 게이트** (분필 `save_item`의 한국어 게이트·복사 게이트와 동일 역할):
1. **PII 재유출 검사** — 마스킹 토큰이 복원됐거나 새 PII 패턴이 초안에 들어갔으면 거부
2. **근거 없는 확약 검사** — `search_policy`/`lookup_order` 결과에 없는 금액·날짜·환불 확약이 포함되면 거부
3. **금지 표현 검사** — 규칙 기반 블랙리스트(법적 확약, 타사 비방, 과장)
4. **정책 인용 존재 검사** — 정책 판단이 필요한 인텐트인데 인용된 정책이 0건이면 거부

거부 시 사유를 도구 응답으로 되돌려 에이전트가 스스로 교정하게 한다(자기교정 루프).

**노드 책임 분리**

| 노드 | 주체 | 판단하는 것 |
|---|---|---|
| `agent` | 생성 LLM | 어떤 도구를 몇 번 부를지, 초안 문장 작성, 제출 시점 |
| `judge` | **별도 백엔드 LLM** | 정책 준수 점수, 톤 적절성 점수, 위반 항목 나열 |
| `validate` | **코드** | judge 점수의 threshold 통과, 근거 인용 존재, PII 검사, 재시도/에스컬레이션 결정 |

> **judge를 별도 노드·별도 백엔드로 두는 이유** — 분필에서 얻은 교훈이다.
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
    judge_result: dict    # {policy_compliance, tone_score, violations[]}
    validation_passed: bool
    validation_feedback: str
    budget: int           # 남은 재시도 횟수 (무한루프 방지)
    outcome: str          # "auto_draft" | "escalated" | "failed"
```

### HITL — 세 가지 종료 상태

분필은 "통과 / 예산 소진 후 종료" 2상태였지만, CS는 **에스컬레이션을 1급 종료 상태로 둔다.** 이게 실제 프로덕션 CS 패턴(컨피던스 임계값 라우팅)과 일치한다.

| `outcome` | 조건 | 상담원이 보는 것 |
|---|---|---|
| `auto_draft` | triage confidence ≥ 임계 & judge 통과 & 코드 검증 통과 | 초안 + 인용 정책 + 사용 도구 |
| `escalated` | triage confidence 미달, 또는 `escalate_to_human` 호출, 또는 budget 소진 | **초안 없음** + 에스컬레이션 사유 |
| `failed` | 파이프라인 예외 | 오류 표시(내부 상세 비노출) |

**초안이 없는 것이 잘못된 초안보다 낫다.** budget 소진 시 마지막 미달 초안을 그냥 내보내지 않는다.

---

## 3. 기술 스택

| 구분 | 선택 |
|---|---|
| 백엔드 | FastAPI (비동기) |
| 오케스트레이션 | LangGraph (reply agent) / 단일 호출 (triage) |
| 벡터스토어 | ChromaDB + Rerank (BGE-reranker) |
| 임베딩 | BGE-M3 (CPU) |
| 생성 LLM | **Anthropic `claude-opus-5`** (기본, `ChatAnthropic`) |
| Judge LLM | **OpenAI `gpt-5.6-luna`** (기본) — 생성과 **다른 벤더**. 채택은 Phase 7 신뢰도 측정으로 확정 |
| 커스텀 어댑터 | Ollama / vLLM 자체 호스팅 / RunPod 서버리스 (`BaseChatModel` 직접 상속) |
| 합성 데이터 DB | SQLite (주문·고객) |
| 프론트엔드 | Next.js (상담원 검토 UI) |
| 트레이싱·eval | LangSmith / Ragas |
| 배포 | Docker + Caddy HTTPS (클라우드 VM) |

**모델 선정 근거 (초기값, 측정 후 갱신)**

- 생성 = `claude-opus-5`: 티켓 처리는 멀티턴 ReAct + 도구 인자 정확도가 관건이고, 잘못된 초안의 비용(고객에게 나가는 답변)이 크다. 비용이 문제가 되면 `claude-sonnet-5`로 내리고 eval로 회귀 여부를 확인한다 — 내리는 판단도 **측정 후에** 한다.
- Judge = 크로스 벤더: "생성 모델이 자기 글을 자기가 채점하지 않는다"의 가장 강한 형태. 같은 벤더의 다른 모델보다 상관성이 낮아 독립적인 판정에 가깝다.
- 모델 비교·확정 데이터는 별도 `MODEL_SELECTION.md`에 누적한다.

> 사용하는 모든 모델 ID는 정확한 문자열로만 쓴다. 날짜 접미사를 임의로 붙이지 않는다.

---

## 4. 데이터

| 항목 | 출처 | 방법 | 용도 |
|---|---|---|---|
| 고객 문의 + 인텐트 라벨 | **Bitext Gen AI Chatbot Customer Support Dataset** (Kaggle/HuggingFace) | 다운로드 | 에이전트 입력 소스 + triage eval ground truth |
| 정책 문서(반품·교환·환불·배송·보증) | **직접 합성** | 가상 이커머스사 규정 문서 작성 | `search_policy` RAG 코퍼스 |
| 주문·고객 레코드 | **직접 합성** | 스크립트 생성 | `lookup_order` / `check_customer_tier` |
| 정책 위반 답변 골든셋 | **직접 합성** | 위반을 의도적으로 심은 답변 | 위반 검출 Recall/F1 평가 |
| 톤 라벨 골든셋 | 생성 답변에 **사람이 직접 라벨링** | 5점 척도 | Judge 신뢰도(κ) 검증 |

**Bitext 데이터셋 정보**: 약 26,872 질문-답변 쌍, 27개 인텐트, 상위 카테고리 = 결제/기술문제/계정관리. 엔티티 슬롯 + 톤(정중체/격식체) 주석 포함. RAG 코퍼스(정책 문서)는 이 데이터셋에 **없으므로** 합성한다.

**⛔ 절대 금지**: 실제 고객 문의, 실제 주문 정보, 식별 가능한 개인정보 수집.

**보호 경로** (사람 승인 없이 에이전트가 수정 불가 — `.claude/hooks/`가 강제):
- `data/raw/` — Bitext 원본
- `evals/golden/` — 정답셋
- `evals/runners/` — eval 실행 스크립트 + 임계값 체크

---

## 5. 평가 설계

**원칙**: ① LLM Judge는 사람 라벨과 먼저 일치율을 검증한다 ② 정량(함수)/정성(Judge·사람)을 분리한다 ③ 골든셋은 코드에 하드코딩하지 않고 `evals/golden/*.jsonl`로 외부화한다.

### 모듈 ① Triage

| 지표 | 판정 | 통과 기준(시작값) |
|---|---|---|
| 인텐트 정확도 | 함수 (Bitext 라벨) | ≥ 0.85 |
| 인텐트 macro-F1 | 함수 | ≥ 0.80 (희소 인텐트 확인용) |
| 카테고리 정확도 | 함수 | ≥ 0.92 |
| confidence 캘리브레이션 | 함수 | 오분류 건의 confidence가 정분류보다 유의하게 낮은가 |

> macro-F1을 같이 보는 이유: 27개 인텐트 분포가 고르지 않아 accuracy만 보면 다수 인텐트에 가려진다.

### 모듈 ② Reply Agent

| 계층 | 지표 | 판정 | 통과 기준(시작값) |
|---|---|---|---|
| 🔴 안전 | PII 마스킹 누락률(FN) | 함수 | **0** |
| 🔴 안전 | 정책 위반 검출 Recall | 함수(골든셋) | ≥ 0.95 |
| 🔴 안전 | 정책 위반 검출 F1 | 함수 | 참고값 |
| 🔴 안전 | 근거 없는 확약률 | 함수(게이트 로그) | **0** |
| 🟡 품질 | 톤 적절성 | LLM Judge | 5점 평균 ≥ 4.0 |
| 🟡 품질 | Judge 신뢰도 (Cohen's κ) | 사람 라벨 대비 | ≥ 0.4 |
| 🟢 검색 | 정책 RAG Recall@5 / MRR | 함수 | R@5 ≥ 0.8 |
| 🟢 과정 | 평균 반복수 · 도구 호출 수 · latency | 함수 | 예산 내 수렴 |
| ⭐ 라우팅 | **에스컬레이션 Recall** | 함수(골든셋) | ≥ 0.9 |
| ⭐ 라우팅 | 에스컬레이션 FP율(과잉 에스컬레이션) | 함수 | 참고값 — 트레이드오프는 사람 판단 |
| 🏁 종단 | 상담원 무수정 채택률 | 사람 | 북극성 |

**에스컬레이션 Recall이 이 프로젝트 고유 지표다.** "사람이 개입해야 했던 케이스를 실제로 넘겼는가". FN(넘겼어야 하는데 자동 초안을 낸 것)이 FP보다 훨씬 위험하므로 Recall을 게이트로, FP율은 참고값으로 둔다.

**Judge 신뢰도를 먼저 검증한다.** 톤·정책 준수는 주관 판정이 섞여 있어, Judge 점수를 게이트로 쓰기 전에 사람 라벨과의 κ를 먼저 측정한다. κ가 목표 미달이면 그 Judge 점수로 통과/재시도를 결정하지 않는다.

**측정 결과는 `EVAL.md`에 이력으로 누적한다.** 모델·프롬프트를 바꿀 때마다 기록한다.

> ⚠️ **분필에서 얻은 함정 하나**: "eval이 존재하는가"와 "내 변경이 eval이 실제로 exercise하는 경로에 있는가"는 별개다. 분필은 생성 프롬프트를 개선했는데 eval 수치가 전혀 변하지 않았고, 원인은 eval이 하드코딩된 고정 출력을 채점하는 구조였기 때문이다. **CS eval은 실제로 파이프라인을 돌려 새 초안을 생성한 뒤 채점하도록 설계한다.**

**개발 루프 = 스모크셋(20~50건), 전체셋 = 사람이 직접 실행.** eval은 실제 API 비용이 나가므로 `--full`은 훅으로 차단한다.

---

## 6. 보안 · 개인정보

- **PII 마스킹은 입력 단계에서, 외부/모델 호출 이전에** 수행한다. 순서를 바꾸지 않는다
- 실제 고객 데이터 미사용 — 전부 공개 데이터셋 + 합성
- 사용자 입력(티켓 본문)은 **비저장**. 영구 저장은 공개/합성 코퍼스뿐
- **로그·캐시에 PII 금지**
- 시크릿은 `.env`(gitignore). `.env.example`만 커밋. 코드에 하드코딩 금지
- 초안 출력에 **"상담원 최종 책임(보조수단)" 고지** 표시 — 분필의 "교사 최종 책임"과 동일 위치
- 감사 로그: 어떤 티켓에 어떤 도구가 호출되고 어떤 정책이 인용됐는지 기록(PII 제외)
- LangSmith 트레이싱은 **마스킹 이후** 단계만. 기본값 비활성(`LANGCHAIN_TRACING_V2=false`), 옵트인

> ⚠️ **외부 전송 트레이드오프 (명시적으로 수용)**: `JUDGE_BACKEND=openai`(기본값)에서는 초안 생성마다 티켓 본문과 초안이 OpenAI로 전송된다. PII 마스킹은 이 호출 이전에 이미 적용돼 있다. 전부 로컬로 처리하려면 `JUDGE_BACKEND=local`로 전환한다 — 단 그 경우 Judge 신뢰도가 재검증 대상이 된다.

---

## 7. 벤더 연동 전략 — 정식 지원 vs 커스텀 어댑터

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

→ 이렇게 하면 "라이브러리를 갖다 쓸 줄 안다"를 넘어 "**LangChain 내부 구조를 이해하고 확장할 수 있다**"를 증명한다.

**산출물**: `VENDOR_INTEGRATION.md` — 언제 정식 통합을 쓰고 언제 커스텀 어댑터가 필요한지, 두 경로의 실제 코드 차이와 함께 정리.

### 파이프라인 규칙

파이프라인 코드에서 `ChatAnthropic`을 **직접 import 하지 않는다.** 항상 `app/common/llm/` 인터페이스를 경유한다. 이 규칙이 깨지면 벤더 전환 실험 자체가 불가능해진다.

---

## 8. 개발 하네스 · 루프 (요약)

전체 설계는 `CS_CLAUDE_CODE_HARNESS_LOOP.md`. 여기서는 제품 설계와 맞물리는 부분만.

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

루프 C는 **자동 종료를 만들지 않는다.** 정책 위반 검출의 FP/FN 균형은 제품 판단이지 기술 판단이 아니다. 제품 쪽 HITL 설계와 같은 논리를 개발 워크플로우에도 적용한다.

> 종료 조건보다 **포기 조건**이 설계하기 어렵고 더 중요하다. 3회 실패 시 멈추고 "무엇을 가정했고 무엇이 틀렸는지"를 보고하게 한다.

**대칭성이 이 프로젝트의 서사다**: 에이전트 제품에 가드레일을 넣는 것과, 에이전트로 개발하는 과정에 가드레일을 넣는 것은 같은 문제다. 둘 다 모델의 협조에 의존하지 않는 시스템 레벨 강제가 필요하다.

---

## 9. 레포 구조

```
cs-assistant/
├── CLAUDE.md                       # 에이전트 행동 규칙 (Layer 1)
├── DESIGN.md                       # 이 문서
├── PROMPTS.md                      # Phase별 빌드 프롬프트
├── EVAL.md                         # 평가 결과 이력
├── VENDOR_INTEGRATION.md           # 정식 통합 vs 커스텀 어댑터 (Phase 8)
├── HARNESS_ENGINEERING.md          # 하네스/루프 회고 (Phase 10)
├── MODEL_SELECTION.md              # 모델 비교 데이터
├── .claude/
│   ├── settings.json               # 공유 훅/권한 (커밋)
│   ├── settings.local.json         # 개인 오버라이드 (gitignore)
│   ├── hooks/                      # block_protected_paths.py 등 (Python)
│   ├── rules/                      # eval-integrity.md, dev-loop.md
│   ├── agents/                     # eval-reviewer.md, prompt-critic.md
│   └── agent-memory/dev/MEMORY.md  # 반복 실패 패턴
├── app/
│   ├── main.py                     # FastAPI
│   ├── common/
│   │   ├── llm/                    # 벤더 추상화 (7절)
│   │   ├── rag/                    # 파싱·청킹·임베딩·리랭킹·ChromaDB
│   │   └── privacy.py              # PII 마스킹
│   └── modules/
│       ├── triage/                 # 모듈 ①
│       └── reply/                  # 모듈 ② graph.py / tools.py / judge.py / state.py
├── prompts/                        # ★ 프롬프트 = 버전 관리 대상, 단독 커밋
├── evals/
│   ├── golden/                     # ★ 보호 경로
│   ├── runners/                    # ★ 보호 경로 (check_thresholds.py 포함)
│   └── reports/                    # 실행 결과 (gitignore)
├── data/
│   ├── raw/                        # ★ 보호 경로 — Bitext 원본
│   └── synthetic/                  # 합성 정책 문서·주문 DB (생성물)
├── frontend/                       # Next.js 상담원 검토 UI
├── tests/
├── scripts/
└── .env.example
```

★ = 보호 경로(사람 승인 없이 에이전트가 수정 불가)

---

## 10. 배포

**구성: 단일 클라우드 VM + Docker + Caddy HTTPS.**

```
브라우저 ─→ Caddy(HTTPS) ─→ Next.js(3000) ─→ FastAPI(8000) ─→ Anthropic API
                                                  │              OpenAI API (judge)
                                                  ├─ ChromaDB (볼륨)
                                                  └─ SQLite (합성 주문/고객)
```

- 생성·Judge 모두 **관리형 API**라 GPU 서버리스가 필수 경로가 아니다 → 분필 대비 인프라가 단순하고 운영비가 낮다
- **RunPod 서버리스는 "커스텀 어댑터 경로 데모"용으로만** 붙인다(상시 운영 아님). `LLM_BACKEND=runpod`로 전환해 동작을 보여주는 용도
- 서버 간 인증: Next.js → FastAPI `CS_API_KEY` 헤더
- **billing alarm 필수** — LLM API는 종량제다
- 배포 사다리: Dockerfile → docker-compose(로컬 검증) → VM 배포 → Caddy HTTPS → GitHub Actions CI

**CI = 최종 게이트**

```yaml
# .github/workflows/ci.yml — 모델 호출 없는 경량 CI (매 PR 블로킹)
- pytest tests/ -q                     # 순수 로직 유닛테스트 (마스킹·게이트 등)
- 백엔드 import 스모크 + 프론트 lint/build
```

전체 eval은 CI에서 자동으로 돌리지 않는다 — 생성·채점 결과의 변동성이 자동 블로킹 게이트에 적합하지 않고, Judge 비용이 실제로 나간다. `evals/runners/check_thresholds.py`는 **사람이 실행**하고, 그 스크립트 자체도 보호 경로에 둔다(임계값을 낮춰 통과시키는 경로 차단).

---

## 11. 빌드 순서

`PROMPTS.md`에 Phase별 프롬프트와 완료 기준이 있다. 순서만 요약하면:

1. **하네스 먼저** — 훅·권한·차단 검증 (코드보다 먼저. 되돌릴 수 없는 사고를 먼저 막는다)
2. 스캐폴딩 → 데이터 준비 → RAG → LLM 추상화(정식 경로)
3. Triage → Reply Agent(judge/validate/escalate)
4. **평가 체계** — Judge 신뢰도부터
5. 커스텀 어댑터 + `VENDOR_INTEGRATION.md`
6. UI → 배포 → CI → `HARNESS_ENGINEERING.md`

---

## 12. 결정 완료 / 미결정

**확정**
- 도메인: 이커머스 CS로 얇게 특화, verticalization 2단계 구조
- 데이터셋: Bitext + 합성 정책/주문 DB
- 생성 백엔드 기본: Anthropic `claude-opus-5` (`ChatAnthropic`)
- Judge: 크로스 벤더(OpenAI `gpt-5.6-luna`) — 신뢰도 측정 후 확정
- 훅 스크립트 언어: **Python (stdlib json)**
- 범위: 풀스택(Next.js UI) + 배포까지 1차 범위
- HITL: `escalated`를 1급 종료 상태로

**미결정**
- 실사용자(테스트해줄 동료) 확보 여부 — 확보되면 "상담원 무수정 채택률" 실측 가능
- 루프 B/C의 구체적 임계값 — Bitext 서브셋 베이스라인 측정 후 확정
- `.claude/agent-memory/`를 커밋할지 (실패 패턴이 포트폴리오 자산이 될 수도 있음)
- 비용 상황에 따른 생성 모델 하향(`claude-sonnet-5`) 여부 — eval 회귀 확인 후 판단
