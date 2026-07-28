"""Reply 에이전트 도구 8개 — 전부 LLM 호출 없이 순수 계산·검색·저장만 한다.
추론과 문장 작성은 에이전트(LLM)가 직접 담당한다(CLAUDE.md 핵심 컨벤션).

세션 스코프 데이터(누적 draft, 도구 호출 로그, 연속 실패 횟수)는 contextvars로
공유한다 — LLM이 호출하는 도구는 LangGraph state를 직접 받지 못하고 자기
인자만 받기 때문이다. bind_session()/init_session() 사용법은 그 함수의
docstring 참고(분필에서 확인된 "노드 안에서 set() 호출 시 전파 안 됨" 함정을
피하는 방법).
"""
import contextvars
import os
import pathlib
import re
import sqlite3

from langchain_core.tools import tool

from app.common.privacy import mask_pii
from app.common.rag.parser import parse_policy_doc
from app.common.rag.singleton import get_retriever

from .routing import requires_policy_citation

# --- 알려진 정책 조항 ID (게이트 ④가 참조) ---------------------------------
# Phase 3의 파서를 재사용해 조항 번호만 뽑는다(정책 문서 파싱 로직을 새로
# 만들지 않는다). 문서 7종·조항 30개 정도라 모듈 로드 시 한 번 계산해도 충분.
_POLICIES_DIR = pathlib.Path("data/synthetic/policies")


def _load_known_clause_ids() -> frozenset:
    ids = set()
    for path in _POLICIES_DIR.glob("*.md"):
        doc = parse_policy_doc(str(path))
        for clause in doc["clauses"]:
            ids.add(clause["clause_id"])
    return frozenset(ids)


KNOWN_CLAUSE_IDS = _load_known_clause_ids()


# --- 세션 컨텍스트 ----------------------------------------------------------
_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("_reply_ctx")


def bind_session() -> None:
    """그래프 실행 전, 그래프 **밖**에서 정확히 한 번 호출한다(graph.py의 run_reply()).

    LangGraph는 각 노드를 격리된 context.run()으로 실행하므로, 노드 안에서
    contextvars.set()을 다시 부르면 다음 노드로 전파되지 않는다(분필
    exam/tools.py의 init_session() 주석에서 확인된 함정과 동일). 그래프가
    시작되기 전에 여기서 빈 dict를 한 번 바인딩해두면, 이후 plan_node는
    같은 dict 객체를 in-place로만 초기화해 모든 노드가 공유하게 된다.
    """
    _ctx.set({})


def init_session(ticket_text: str, order_id: str, intent: str) -> None:
    """plan_node에서 호출 — 이미 바인딩된 dict를 in-place로 초기화한다."""
    ctx = _ctx.get()
    ctx.clear()
    ctx.update({
        "ticket_text": ticket_text,
        "order_id": order_id,
        "intent": intent,
        "draft_text": "",
        "cited_policies": [],
        "tools_used": [],
        "tool_results_log": [],  # 게이트②가 참조하는 "실제로 조회된 사실" 로그
        "save_draft_fail_streak": 0,
        "escalate_requested": False,
        "escalate_reason_text": "",
        "order_not_found": False,
        "submitted": False,
    })


def get_ctx() -> dict:
    return _ctx.get()


def _db_path() -> str:
    return os.getenv("SHOP_DB_PATH", "./data/synthetic/shop.db")


# --- 조회 도구 3종 -----------------------------------------------------------

@tool
def search_policy(query: str) -> str:
    """Search Northwind Retail policy documents (returns, refunds, shipping,
    cancellation fees, warranty, payment methods, membership tiers) for
    relevant clauses. query: search keywords describing what you need."""
    ctx = get_ctx()
    ctx["tools_used"].append("search_policy")
    results = get_retriever().retrieve(query, "policies", top_k=3)
    if not results:
        result = "No relevant policy found."
    else:
        # 청크 텍스트는 이미 "[RET-02] Title\n<body>" 형식(Phase 3 chunker) —
        # 에이전트가 이 조항 ID를 그대로 답변에 인용할 수 있다.
        result = "\n\n".join(r["text"] for r in results)
    ctx["tool_results_log"].append(result)
    return result


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order's status, carrier, tracking number, dates, and amount.
    order_id: the order identifier, e.g. ORD-000123."""
    ctx = get_ctx()
    ctx["tools_used"].append("lookup_order")

    conn = sqlite3.connect(_db_path())
    try:
        row = conn.execute(
            "SELECT order_id, status, carrier, tracking_no, ordered_at, "
            "delivered_at, amount, currency FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        ctx["order_not_found"] = True
        result = f"No order found with ID {order_id}."
    else:
        oid, status, carrier, tracking_no, ordered_at, delivered_at, amount, currency = row
        result = (
            f"Order {oid}: status={status}, carrier={carrier}, "
            f"tracking={tracking_no}, ordered_at={ordered_at}, "
            f"delivered_at={delivered_at or 'not yet delivered'}, "
            f"amount=${amount:.2f} {currency}"
        )
    ctx["tool_results_log"].append(result)
    return result


@tool
def check_customer_tier(customer_id: str) -> str:
    """Look up a customer's membership tier (standard/plus/vip).
    customer_id: the customer identifier, e.g. CUST-000123."""
    ctx = get_ctx()
    ctx["tools_used"].append("check_customer_tier")

    conn = sqlite3.connect(_db_path())
    try:
        row = conn.execute(
            "SELECT tier FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
    finally:
        conn.close()

    result = (
        f"No customer found with ID {customer_id}."
        if row is None
        else f"Customer {customer_id} tier: {row[0]}."
    )
    ctx["tool_results_log"].append(result)
    return result


# --- 형식 검증 ---------------------------------------------------------------

_MIN_WORDS = 15
_KNOWN_MASK_TOKENS = {"{{EMAIL}}", "{{PHONE}}", "{{CARD}}", "{{ADDRESS}}", "{{NAME}}"}
_PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")


def _format_errors(reply_text: str) -> list[str]:
    errors = []
    if not reply_text.strip():
        errors.append("reply is empty")
        return errors
    word_count = len(reply_text.split())
    if word_count < _MIN_WORDS:
        errors.append(f"reply is too short ({word_count} words, need at least {_MIN_WORDS})")
    for m in _PLACEHOLDER_RE.finditer(reply_text):
        if m.group(0) not in _KNOWN_MASK_TOKENS:
            errors.append(f"reply contains an unresolved placeholder: {m.group(0)}")
            break
    return errors


@tool
def validate_draft_format(reply_text: str) -> str:
    """Check the reply's basic structure (minimum length, no leftover
    unresolved placeholders). Call this before save_draft to self-correct
    format issues early."""
    errors = _format_errors(reply_text)
    if errors:
        return "Format check failed: " + " / ".join(errors)
    return "Format check passed."


# --- save_draft 결정론적 게이트 5종 ------------------------------------------
# 전부 코드 판단(LLM 판단 아님). 통과해야만 draft_text가 실제로 갱신된다 —
# 이후 validate_node는 이 게이트들을 다시 검사하지 않는다(이미 강제됐으므로).

_MONEY_RE = re.compile(r"\$\s?\d+(?:\.\d{2})?")
_FORBIDDEN_PHRASES = (
    "guarantee", "guaranteed", "we are liable", "we're liable",
    "legally binding", "100% refund", "we promise", "we assure you",
)

# CLAUDE.md 보안 하드룰⑥(상담원 최종 책임 고지)이 실제로 초안에 남는지 강제한다.
# prompts/reply_agent.md는 모델에게 이 문장을 그대로 쓰라고 지시하지만, 로컬
# 모델은 가끔 이걸 빼먹은 채 답변을 끝낸다(2026-07-28 확인) — 프롬프트 지시만
# 믿지 않고 게이트로 강제한다(이 프로젝트의 핵심 원칙: 판단은 LLM, 통과 여부는
# 코드). 줄바꿈/공백 차이는 허용하되 문구 자체는 그대로 요구한다.
_DISCLAIMER_RE = re.compile(
    r"this\s+is\s+a\s+draft\s+prepared\s+by\s+an\s+ai\s+assistant\.\s*"
    r"a\s+human\s+agent\s+is\s+responsible\s+for\s+reviewing\s+and\s+approving\s+"
    r"it\s+before\s+it\s+is\s+sent\.?",
    re.IGNORECASE,
)


def _gate_pii_leak(reply_text: str) -> str | None:
    """① 마스킹되지 않은 원본 PII 패턴만 거부한다. 마스킹 토큰({{EMAIL}})은
    mask_pii()가 멱등이라 자연히 통과한다."""
    if mask_pii(reply_text) != reply_text:
        return (
            "Rejected — the reply contains unmasked personal information "
            "(email/phone/card/address/name). Keep masking tokens as-is; "
            "do not restate the customer's real contact details."
        )
    return None


def _gate_unsupported_commitment(reply_text: str, ctx: dict) -> str | None:
    """② 이번 세션에서 실제로 조회된 적 없는 금액을 확약하면 거부한다.

    한계(v1): 금액만 검사한다. 날짜 확약 검증은 날짜 표기가 워낙 다양해
    (2026-08-01 / Aug 1 / in 3 days …) 오탐 없이 대조하기 어려워 범위 밖으로
    뒀다 — 필요해지면 lookup_order 반환값의 날짜 필드와 정규화 비교로 확장.
    """
    combined_log = " ".join(ctx["tool_results_log"])
    for match in _MONEY_RE.finditer(reply_text):
        amount = match.group(0).replace(" ", "")
        if amount not in combined_log:
            return (
                f"Rejected — the amount {amount} in your reply was not returned by "
                "any tool (search_policy/lookup_order) this session. Only state "
                "figures you actually retrieved."
            )
    return None


def _gate_forbidden_phrases(reply_text: str) -> str | None:
    """③ 법적 확약·무조건 보상 표현 블랙리스트.

    한계(v1): 타사 비방은 알려진 경쟁사 명단이 없어 일반적으로 탐지하기
    어려워 범위 밖으로 뒀다 — 법적 확약 언어만 결정론적으로 잡는다.
    """
    lowered = reply_text.lower()
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in lowered:
            return (
                f"Rejected — the reply contains a forbidden commitment phrase "
                f"('{phrase}'). Do not make legal guarantees or absolute promises."
            )
    return None


def _gate_missing_citation(reply_text: str, intent: str) -> str | None:
    """④ 정책 인용이 필수인 인텐트인데 알려진 조항 ID가 하나도 없으면 거부."""
    if not requires_policy_citation(intent):
        return None
    if not any(cid in reply_text for cid in KNOWN_CLAUSE_IDS):
        return (
            "Rejected — this ticket requires a policy citation (e.g. [RET-02]) "
            "but none was found in the reply. Call search_policy and quote the "
            "clause ID it returns."
        )
    return None


def _gate_missing_disclaimer(reply_text: str) -> str | None:
    """⑤ 상담원 최종 책임 고지 문구가 그대로 없으면 거부(CLAUDE.md 보안 하드룰⑥)."""
    if not _DISCLAIMER_RE.search(reply_text):
        return (
            "Rejected — the reply is missing the required disclaimer. End your "
            "reply with this exact line: \"This is a draft prepared by an AI "
            "assistant. A human agent is responsible for reviewing and "
            "approving it before it is sent.\""
        )
    return None


@tool
def save_draft(reply_text: str) -> str:
    """Save the draft reply. Rejected if it fails any of five checks: leaked
    personal information, a dollar amount not backed by any tool result, a
    forbidden legal-commitment phrase, (when required for this ticket's
    intent) a missing policy citation, or a missing human-review disclaimer.
    On rejection, revise the reply according to the stated reason and call
    save_draft again."""
    ctx = get_ctx()

    rejection = (
        _gate_pii_leak(reply_text)
        or _gate_unsupported_commitment(reply_text, ctx)
        or _gate_forbidden_phrases(reply_text)
        or _gate_missing_citation(reply_text, ctx["intent"])
        or _gate_missing_disclaimer(reply_text)
    )
    if rejection:
        ctx["save_draft_fail_streak"] += 1
        return rejection

    ctx["save_draft_fail_streak"] = 0
    ctx["draft_text"] = reply_text
    ctx["cited_policies"] = [cid for cid in KNOWN_CLAUSE_IDS if cid in reply_text]
    ctx["tools_used"].append("save_draft")
    return "Draft saved."


@tool
def discard_draft() -> str:
    """Discard the currently saved draft so you can write a new one from scratch."""
    ctx = get_ctx()
    ctx["draft_text"] = ""
    ctx["cited_policies"] = []
    ctx["tools_used"].append("discard_draft")
    return "Draft discarded."


@tool
def escalate_to_human(reason: str) -> str:
    """Signal that this ticket cannot be handled automatically and must go to
    a human agent instead of receiving a draft reply. reason: a short
    explanation of why."""
    ctx = get_ctx()
    ctx["escalate_requested"] = True
    ctx["escalate_reason_text"] = reason
    ctx["tools_used"].append("escalate_to_human")
    return "Escalation recorded."


@tool
def submit_for_review() -> str:
    """Signal that the draft is complete and ready for human review. Call this
    only after save_draft has succeeded — calling it with no saved draft is
    rejected."""
    ctx = get_ctx()
    if not ctx["draft_text"]:
        return "Rejected — no draft has been saved yet. Call save_draft first."
    ctx["submitted"] = True
    ctx["tools_used"].append("submit_for_review")
    return "Submitted for review."


TOOLS = [
    search_policy,
    lookup_order,
    check_customer_tier,
    validate_draft_format,
    save_draft,
    discard_draft,
    escalate_to_human,
    submit_for_review,
]
