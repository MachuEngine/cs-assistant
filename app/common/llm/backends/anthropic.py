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
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> ChatAnthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
    return ChatAnthropic(
        model=model or os.getenv("ANTHROPIC_MODEL", "claude-opus-5"),
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )


class AnthropicJudgeBackend(LLMBackend):
    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-opus-5")

    async def generate(self, messages: list[dict], **kwargs) -> str:
        chat = get_chat_anthropic(
            model=self.model,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 2048),
        )
        lc_messages = [_ROLE_MAP.get(m["role"], HumanMessage)(content=m["content"]) for m in messages]
        result = await chat.ainvoke(lc_messages)
        return result.content or ""
