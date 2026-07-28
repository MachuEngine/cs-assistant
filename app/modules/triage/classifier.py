"""티켓 분류 — 단일 LLM 호출 + 구조화 출력. 에이전트를 쓰지 않는다(DESIGN.md 2절).

"여기에도 ReAct를 넣자"는 제안 금지 — 분류에는 도구 호출이나 다단계 추론이
필요 없다. 에이전트가 필요 없는 곳에 안 쓰는 것도 설계 판단이다.
"""
import pathlib

from pydantic import BaseModel, Field

from app.common.llm import get_llm_backend
from app.common.privacy import mask_pii
from app.modules.reply.routing import ALL_CATEGORIES, ALL_INTENTS, check_pre_agent_escalation

_PROMPT_PATH = pathlib.Path("prompts/triage_classify.md")


class TriageResult(BaseModel):
    intent: str = Field(description="one of the 27 known intents")
    category: str = Field(description="one of the 11 known categories")
    confidence: float = Field(ge=0.0, le=1.0, description="model's self-reported confidence")
    reason: str = Field(description="one-sentence justification for the classification")


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


async def classify_ticket(masked_text: str) -> TriageResult:
    """이미 마스킹된 티켓 본문을 분류한다.

    마스킹 책임은 호출부(triage_ticket)에 있다 — 이 함수는 마스킹 여부를
    검사하지 않는다. 직접 호출하지 말고 triage_ticket()을 쓸 것.
    """
    llm = get_llm_backend()
    structured_llm = llm.with_structured_output(TriageResult)
    result = await structured_llm.ainvoke([
        {"role": "system", "content": _load_prompt()},
        {"role": "user", "content": masked_text},
    ])

    if result.intent not in ALL_INTENTS:
        raise ValueError(f"모델이 알 수 없는 인텐트를 반환함: {result.intent!r}")
    if result.category not in ALL_CATEGORIES:
        raise ValueError(f"모델이 알 수 없는 카테고리를 반환함: {result.category!r}")

    return result


async def triage_ticket(raw_text: str, flags: str = "") -> dict:
    """티켓 원문을 받아 마스킹 후 분류까지 끝낸다 — 이 모듈의 공개 진입점.

    [엄수] 마스킹은 이 함수 내부에서, 모델 호출(classify_ticket) 이전에
    실행된다. 호출부가 마스킹 호출을 깜빡할 여지를 없애기 위해 여기서
    강제한다.

    confidence는 LLM이 기록만 하고, requires_human 판정(E1~E4)은 코드가
    한다(app.modules.reply.routing.check_pre_agent_escalation).
    """
    masked_text = mask_pii(raw_text)
    result = await classify_ticket(masked_text)

    escalation_reason = check_pre_agent_escalation(result.intent, result.confidence, flags)

    return {
        "intent": result.intent,
        "category": result.category,
        "confidence": result.confidence,
        "requires_human": escalation_reason is not None,
        "reason": result.reason,
        "escalation_reason": escalation_reason,
    }
