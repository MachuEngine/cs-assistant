"""라우팅 규칙 — 에스컬레이션 조건과 인텐트→도구 매핑을 한 곳에 둔다.

DESIGN.md 3.1·3.2절. 프롬프트·save_draft 게이트·eval이 전부 이 모듈을
참조해야 한다 — 표를 복제하지 말 것. scripts/hydrate_tickets.py의
ORDER_LINKED_INTENTS도 이 표(LOOKUP_ORDER_REQUIRED)와 동일해야 한다.
"""
import os

# --- 3.2 인텐트 → 도구 매핑 ------------------------------------------------
# "필수" 표시된 인텐트 집합. search_policy가 필수인 인텐트는 곧 save_draft
# 게이트 ④(정책 인용 존재)가 검사하는 대상이기도 하다.
SEARCH_POLICY_REQUIRED = frozenset({
    "cancel_order", "change_order",
    "check_cancellation_fee",
    "check_refund_policy", "get_refund",
    "delivery_options", "delivery_period",
    "change_shipping_address",
    "check_payment_methods",
})

LOOKUP_ORDER_REQUIRED = frozenset({
    "cancel_order", "change_order", "track_order",
    "check_cancellation_fee",
    "get_refund", "track_refund",
    "change_shipping_address",
    "payment_issue",
    "check_invoice", "get_invoice",
})

CHECK_CUSTOMER_TIER_REQUIRED = frozenset({
    "check_cancellation_fee",
    "get_refund",
    "delivery_options",
})

# 27개 인텐트 → 카테고리 (Bitext 실측, DESIGN.md 4.1절)
INTENT_TO_CATEGORY = {
    "create_account": "ACCOUNT",
    "delete_account": "ACCOUNT",
    "edit_account": "ACCOUNT",
    "switch_account": "ACCOUNT",
    "recover_password": "ACCOUNT",
    "registration_problems": "ACCOUNT",
    "check_cancellation_fee": "CANCEL",
    "contact_human_agent": "CONTACT",
    "contact_customer_service": "CONTACT",
    "delivery_options": "DELIVERY",
    "delivery_period": "DELIVERY",
    "complaint": "FEEDBACK",
    "review": "FEEDBACK",
    "check_invoice": "INVOICE",
    "get_invoice": "INVOICE",
    "cancel_order": "ORDER",
    "change_order": "ORDER",
    "place_order": "ORDER",
    "track_order": "ORDER",
    "check_payment_methods": "PAYMENT",
    "payment_issue": "PAYMENT",
    "check_refund_policy": "REFUND",
    "get_refund": "REFUND",
    "track_refund": "REFUND",
    "change_shipping_address": "SHIPPING",
    "set_up_shipping_address": "SHIPPING",
    "newsletter_subscription": "SUBSCRIPTION",
}

ALL_INTENTS = frozenset(INTENT_TO_CATEGORY)
ALL_CATEGORIES = frozenset(INTENT_TO_CATEGORY.values())


def requires_search_policy(intent: str) -> bool:
    return intent in SEARCH_POLICY_REQUIRED


def requires_lookup_order(intent: str) -> bool:
    return intent in LOOKUP_ORDER_REQUIRED


def requires_check_customer_tier(intent: str) -> bool:
    return intent in CHECK_CUSTOMER_TIER_REQUIRED


def requires_policy_citation(intent: str) -> bool:
    """save_draft 게이트 ④가 검사하는 대상 — search_policy 필수 인텐트와 동일 집합."""
    return requires_search_policy(intent)


# --- 3.1 에스컬레이션 기준 --------------------------------------------------
# E5~E8은 reply 에이전트 루프(Phase 6) 상태가 있어야 판정 가능해 여기서는
# 사유 라벨만 정의한다. 실제 판정 로직은 app/modules/reply/graph.py에 있다.
ESCALATION_REASONS = {
    "E1": "triage confidence below threshold",
    "E2": "customer explicitly requested a human agent",
    "E3": "complaint intent — compensation/liability judgment is out of policy scope",
    "E4": "offensive language flag detected",
    "E5": "agent explicitly escalated",
    "E6": "referenced order could not be found",
    "E7": "save_draft gate failed 3 consecutive times",
    "E8": "retry budget exhausted without passing validation",
}

OFFENSIVE_FLAG = "W"
HUMAN_REQUEST_INTENT = "contact_human_agent"
COMPLAINT_INTENT = "complaint"


def get_triage_confidence_threshold() -> float:
    return float(os.getenv("TRIAGE_CONFIDENCE_THRESHOLD", "0.70"))


def check_pre_agent_escalation(intent: str, confidence: float, flags: str = "") -> str | None:
    """triage 직후 판정 가능한 에스컬레이션 조건(E1~E4). 해당 없으면 None.

    조건은 이 순서로 확인한다 — confidence 미달이 가장 먼저(가장 근본적인
    불확실성이라, 인텐트가 뭐든 사람에게 넘기는 게 맞다).
    """
    if confidence < get_triage_confidence_threshold():
        return "E1"
    if intent == HUMAN_REQUEST_INTENT:
        return "E2"
    if intent == COMPLAINT_INTENT:
        return "E3"
    if OFFENSIVE_FLAG in (flags or ""):
        return "E4"
    return None
