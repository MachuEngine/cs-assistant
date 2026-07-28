"""LLM 벤더 추상화 진입점.

LLM_BACKEND(생성)와 JUDGE_BACKEND(채점)는 서로 독립적으로 전환된다.
- get_llm_backend()는 LangChain BaseChatModel을 반환한다 — reply 에이전트의
  bind_tools()에 필요하다(Phase 6).
- get_judge_backend()는 LLMBackend(단순 generate())를 반환한다 — judge.py가
  런타임·오프라인 eval 양쪽에서 동일하게 호출한다(DESIGN.md 3.4절).

파이프라인 코드는 이 팩토리를 경유해야 하며 ChatAnthropic/ChatOpenAI 등을
직접 import 하지 않는다(CLAUDE.md 핵심 컨벤션) — 그래야 벤더 전환·커스텀
어댑터(Phase 8) 실험이 가능하다.

키가 없거나 호출이 실패하면 조용히 폴백하지 않고 그대로 실패한다(fail-fast).
"""
import os

from langchain_core.language_models import BaseChatModel

from .backends.anthropic import AnthropicJudgeBackend, get_chat_anthropic
from .backends.ollama import OllamaJudgeBackend, get_chat_ollama
from .backends.openai import OpenAIJudgeBackend, get_chat_openai
from .base import LLMBackend


def get_llm_backend() -> BaseChatModel:
    backend = os.getenv("LLM_BACKEND", "anthropic")
    if backend == "anthropic":
        return get_chat_anthropic()
    if backend == "openai":
        return get_chat_openai()
    if backend == "ollama":
        return get_chat_ollama()
    raise NotImplementedError(f"지원하지 않는 LLM_BACKEND: '{backend}'")


def get_judge_backend() -> LLMBackend:
    backend = os.getenv("JUDGE_BACKEND", "openai")
    if backend == "openai":
        return OpenAIJudgeBackend()
    if backend == "anthropic":
        return AnthropicJudgeBackend()
    if backend == "ollama":
        return OllamaJudgeBackend()
    raise NotImplementedError(f"지원하지 않는 JUDGE_BACKEND: '{backend}'")
