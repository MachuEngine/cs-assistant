"""Anthropic 백엔드 — 정식 지원 경로(langchain-anthropic). 기본 생성 백엔드.

get_chat_anthropic()은 LangChain BaseChatModel을 그대로 반환한다(reply
에이전트의 bind_tools()에 필요, Phase 6). AnthropicJudgeBackend는 같은 모델을
LLMBackend 인터페이스(단순 generate())로 감싼 것 — JUDGE_BACKEND=anthropic으로
전환할 때만 쓰인다(기본 Judge는 openai).

키가 없으면 조용히 넘어가지 않고 즉시 실패한다(fail-fast) — DESIGN.md 3절.
"""
import os

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..base import LLMBackend

_ROLE_MAP = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}


def get_chat_anthropic(
    model: str | None = None,
    max_tokens: int = 2048,
) -> ChatAnthropic:
    # temperature를 안 보낸다 — claude-sonnet-5(그 외 adaptive thinking을 쓰는
    # 최신 모델군)는 이 파라미터를 보내면 400 "temperature is deprecated for
    # this model"로 거부한다(2026-07-29 실측, 실제 API 호출로 확인). 과거
    # 모델은 온도 조절이 유효했지만 지금 기본 모델에서는 아예 안 통한다.
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
    return ChatAnthropic(
        model=model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        api_key=api_key,
        max_tokens=max_tokens,
    )


class AnthropicJudgeBackend(LLMBackend):
    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

    async def generate(self, messages: list[dict], **kwargs) -> str:
        chat = get_chat_anthropic(
            model=self.model,
            max_tokens=kwargs.get("max_tokens", 2048),
        )
        lc_messages = [_ROLE_MAP.get(m["role"], HumanMessage)(content=m["content"]) for m in messages]
        result = await chat.ainvoke(lc_messages)
        return result.content or ""
