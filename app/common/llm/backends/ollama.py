"""Ollama 백엔드 — 로컬 개발용. 공식 langchain-ollama 통합을 그대로 쓴다.

RunPod(Phase 8)와 달리 Ollama는 공식 LangChain 통합이 있어 커스텀 어댑터가
필요 없다(DESIGN.md 10절 판단 기준: 단일 동기 요청-응답).
"""
import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from ..base import LLMBackend

_ROLE_MAP = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}


def get_chat_ollama(model: str | None = None, temperature: float = 0.7) -> ChatOllama:
    return ChatOllama(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        model=model or os.getenv("OLLAMA_MODEL", "qwen2.5:14b"),
        temperature=temperature,
    )


class OllamaJudgeBackend(LLMBackend):
    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:14b")

    async def generate(self, messages: list[dict], **kwargs) -> str:
        chat = get_chat_ollama(model=self.model, temperature=kwargs.get("temperature", 0.7))
        lc_messages = [_ROLE_MAP.get(m["role"], HumanMessage)(content=m["content"]) for m in messages]
        result = await chat.ainvoke(lc_messages)
        return result.content or ""
