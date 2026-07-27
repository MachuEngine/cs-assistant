"""OpenAI Chat Completions 백엔드 어댑터 (LangChain 호환).

RunPod는 공식 LangChain 통합이 없어 chat_runpod.py가 httpx로 직접 구현했지만,
OpenAI는 langchain_openai가 이미 검증된 공식 BaseChatModel 통합(tool-calling
파싱·재시도·스트리밍 포함)을 제공하므로 이를 얇게 감싸기만 한다.
"""
import os

from langchain_openai import ChatOpenAI


class ChatOpenAIBackend(ChatOpenAI):
    """OPENAI_API_KEY/OPENAI_MODEL 환경변수 기본값을 프로젝트 관례에 맞춘 얇은 래퍼."""

    def __init__(self, temperature: float = 0.7, max_tokens: int = 2048, model: str | None = None, **kwargs):
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        super().__init__(
            model=model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
