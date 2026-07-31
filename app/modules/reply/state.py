from typing_extensions import TypedDict


class TicketInput(TypedDict):
    ticket_id: str
    text: str          # 마스킹된 티켓 본문
    customer_id: str
    order_id: str      # 없을 수 있음("")


class TriageInfo(TypedDict):
    intent: str
    category: str
    confidence: float
    requires_human: bool


class Draft(TypedDict):
    reply_text: str
    cited_policies: list  # ["RET-02", ...] — save_draft가 본문에서 추출
    tools_used: list       # ["search_policy", "lookup_order", ...]


class JudgeResult(TypedDict):
    policy_compliance: int   # 1-5
    tone: int                # 1-5
    violations: list         # [{type, span, severity}]
    reasoning: str


class ReplyState(TypedDict):
    """DESIGN.md 2절. plan → agent → judge → validate → (retry | escalate | end)."""

    ticket: TicketInput
    triage: TriageInfo
    draft: Draft
    judge_result: JudgeResult
    validation_passed: bool
    validation_feedback: str
    budget: int                # REPLY_BUDGET+1로 초기화(오프셋은 graph.py 주석 참고)
    outcome: str                # "auto_draft" | "escalated" | "failed"
    escalation_reason: str      # "E1".."E9" 또는 ""
