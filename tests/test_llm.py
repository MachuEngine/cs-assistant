"""LLM 추상화 레이어 테스트 (Phase 4 완료 기준).

Anthropic/OpenAI는 실제 API 키가 없어 fail-fast 동작과 클래스 스위칭만
확인한다(네트워크 호출 없음 — LangChain 생성자는 키 존재만 확인하고
네트워크는 첫 invoke()에서만 친다). Ollama는 로컬에 실행 중인 서버로
실제 왕복까지 확인한다.
"""
import os

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from app.common.llm import get_judge_backend, get_llm_backend
from app.common.llm.backends.ollama import OllamaJudgeBackend


@pytest.fixture
def clear_llm_env(monkeypatch):
    for key in ("LLM_BACKEND", "JUDGE_BACKEND", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    yield monkeypatch


def test_llm_backend_switch_returns_correct_class(clear_llm_env):
    clear_llm_env.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy-for-construction-only")
    clear_llm_env.setenv("LLM_BACKEND", "anthropic")
    assert isinstance(get_llm_backend(), ChatAnthropic)

    clear_llm_env.setenv("OPENAI_API_KEY", "sk-dummy-for-construction-only")
    clear_llm_env.setenv("LLM_BACKEND", "openai")
    assert isinstance(get_llm_backend(), ChatOpenAI)

    clear_llm_env.setenv("LLM_BACKEND", "ollama")
    assert isinstance(get_llm_backend(), ChatOllama)


def test_judge_backend_switch_returns_correct_class(clear_llm_env):
    clear_llm_env.setenv("OPENAI_API_KEY", "sk-dummy-for-construction-only")
    clear_llm_env.setenv("JUDGE_BACKEND", "openai")
    from app.common.llm.backends.openai import OpenAIJudgeBackend
    assert isinstance(get_judge_backend(), OpenAIJudgeBackend)

    clear_llm_env.setenv("JUDGE_BACKEND", "ollama")
    assert isinstance(get_judge_backend(), OllamaJudgeBackend)


def test_missing_api_key_fails_fast(clear_llm_env):
    """키가 없으면 조용히 폴백하지 말고 즉시 RuntimeError — DESIGN.md 3절."""
    clear_llm_env.setenv("LLM_BACKEND", "anthropic")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        get_llm_backend()

    clear_llm_env.setenv("JUDGE_BACKEND", "openai")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        import asyncio
        asyncio.run(get_judge_backend().generate([{"role": "user", "content": "hi"}]))


def test_unknown_backend_raises(clear_llm_env):
    clear_llm_env.setenv("LLM_BACKEND", "not-a-real-backend")
    with pytest.raises(NotImplementedError):
        get_llm_backend()


@pytest.mark.llm_live
def test_ollama_generate_end_to_end(clear_llm_env):
    """실제 로컬 Ollama 서버로 왕복 — LLM_BACKEND/JUDGE_BACKEND 둘 다 확인."""
    import asyncio

    clear_llm_env.setenv("LLM_BACKEND", "ollama")
    clear_llm_env.setenv("OLLAMA_MODEL", "qwen2.5:14b")
    chat = get_llm_backend()
    result = chat.invoke("Reply with exactly one word: OK")
    assert result.content.strip()

    clear_llm_env.setenv("JUDGE_BACKEND", "ollama")
    judge = get_judge_backend()
    text = asyncio.run(judge.generate([
        {"role": "user", "content": "Reply with exactly one word: OK"}
    ]))
    assert text.strip()
