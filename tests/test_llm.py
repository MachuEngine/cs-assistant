"""LLM 추상화 레이어 테스트 (Phase 4 완료 기준).

Anthropic/OpenAI는 실제 API 키가 없어 fail-fast 동작과 클래스 스위칭만
확인한다(네트워크 호출 없음 — LangChain 생성자는 키 존재만 확인하고
네트워크는 첫 invoke()에서만 친다). Ollama는 로컬에 실행 중인 서버로
실제 왕복까지 확인한다.
"""
import json
import os

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from app.common.llm import get_judge_backend, get_llm_backend
from app.common.llm.backends.chat_runpod import ChatRunPod, _build_ai_message, _to_runpod_messages
from app.common.llm.backends.ollama import OllamaJudgeBackend
from app.common.llm.backends.runpod import RunPodBackend


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

    # RunPod은 커스텀 어댑터(DESIGN.md 10절) — 생성자 자체는 키 없이도 되고,
    # 키 체크는 실제 호출 시점(RunPodBackend._call_raw)에서만 fail-fast 한다.
    clear_llm_env.setenv("LLM_BACKEND", "runpod")
    assert isinstance(get_llm_backend(), ChatRunPod)


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


# --- RunPod 커스텀 어댑터 — 순수 함수만(HTTP 호출 없음, VENDOR_INTEGRATION.md 참고) ---

def test_to_runpod_messages_converts_roles_and_content():
    messages = [SystemMessage(content="sys"), HumanMessage(content="hi")]
    result = _to_runpod_messages(messages)
    assert result == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]


def test_to_runpod_messages_converts_tool_calls_to_openai_format():
    ai = AIMessage(content="", tool_calls=[{"id": "call_1", "name": "search_policy", "args": {"query": "returns"}, "type": "tool_call"}])
    result = _to_runpod_messages([ai])
    assert result[0]["role"] == "assistant"
    assert result[0]["tool_calls"][0]["id"] == "call_1"
    assert result[0]["tool_calls"][0]["function"]["name"] == "search_policy"
    assert json.loads(result[0]["tool_calls"][0]["function"]["arguments"]) == {"query": "returns"}


def test_to_runpod_messages_preserves_tool_call_id():
    result = _to_runpod_messages([ToolMessage(content="found", tool_call_id="call_1")])
    assert result[0]["role"] == "tool"
    assert result[0]["tool_call_id"] == "call_1"


def test_build_ai_message_plain_text():
    msg = _build_ai_message({"response": "hello"})
    assert msg.content == "hello"
    assert msg.tool_calls == []


def test_build_ai_message_with_tool_calls():
    raw = {
        "response": "",
        "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "lookup_order", "arguments": '{"order_id": "ORD-000001"}'},
        }],
    }
    msg = _build_ai_message(raw)
    assert msg.tool_calls[0]["name"] == "lookup_order"
    assert msg.tool_calls[0]["args"] == {"order_id": "ORD-000001"}


def test_runpod_backend_payload_includes_tools_and_stop():
    backend = RunPodBackend()
    payload = backend._payload(
        [{"role": "user", "content": "hi"}],
        max_tokens=100, temperature=0.2,
        tools=[{"type": "function", "function": {"name": "x"}}],
        stop=["\n\n"],
    )
    assert payload["input"]["max_tokens"] == 100
    assert payload["input"]["tools"][0]["function"]["name"] == "x"
    assert payload["input"]["stop"] == ["\n\n"]


@pytest.mark.asyncio
async def test_runpod_backend_call_raw_fails_fast_without_keys(clear_llm_env):
    clear_llm_env.delenv("RUNPOD_API_KEY", raising=False)
    clear_llm_env.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    backend = RunPodBackend()
    with pytest.raises(RuntimeError, match="RUNPOD_API_KEY"):
        await backend.generate([{"role": "user", "content": "hi"}])


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
