"""OpenAI 텍스트 전용 백엔드 (LLMBackend 인터페이스). chat_openai.py의 LangChain
어댑터를 내부적으로 재사용 — tool calling이 필요없는 단순 generate() 호출용."""
import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..base import LLMBackend
from .chat_openai import ChatOpenAIBackend

_ROLE_MAP = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}


class OpenAIBackend(LLMBackend):
    def __init__(self, model=None):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    async def generate(self, messages: list[dict], **kwargs) -> str:
        chat = ChatOpenAIBackend(
            model=self.model,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 2048),
        )
        lc_messages = [_ROLE_MAP.get(m["role"], HumanMessage)(content=m["content"]) for m in messages]
        result = await chat.ainvoke(lc_messages)
        return result.content or ""
