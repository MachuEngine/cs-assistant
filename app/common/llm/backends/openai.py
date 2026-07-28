"""OpenAI 백엔드 — 정식 지원 경로(langchain-openai). 기본 Judge 백엔드.

생성 모델과 다른 벤더를 Judge로 쓰는 것이 이 프로젝트의 핵심 설계 원칙이다
("생성 모델이 자기 글을 자기가 채점하지 않는다", DESIGN.md 2절). get_chat_openai()는
LangChain BaseChatModel을 반환하고, OpenAIJudgeBackend는 이를 LLMBackend
인터페이스(단순 generate())로 감싼다 — judge.py가 실제로 쓰는 것은 이쪽이다.

키가 없으면 조용히 넘어가지 않고 즉시 실패한다(fail-fast) — DESIGN.md 3절.
"""
import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..base import LLMBackend

_ROLE_MAP = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}


def get_chat_openai(
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> ChatOpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
    return ChatOpenAI(
        model=model or os.getenv("OPENAI_JUDGE_MODEL", "gpt-5.6-luna"),
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )


class OpenAIJudgeBackend(LLMBackend):
    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("OPENAI_JUDGE_MODEL", "gpt-5.6-luna")

    async def generate(self, messages: list[dict], **kwargs) -> str:
        chat = get_chat_openai(
            model=self.model,
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 2048),
        )
        lc_messages = [_ROLE_MAP.get(m["role"], HumanMessage)(content=m["content"]) for m in messages]
        result = await chat.ainvoke(lc_messages)
        return result.content or ""
