"""RunPod 서버리스를 LangChain BaseChatModel로 감싼 커스텀 어댑터(DESIGN.md 10절).

reply agent(LangGraph)는 LangChain 인터페이스(bind_tools/ainvoke)가 필요하므로
RunPodBackend(app/common/llm/backends/runpod.py) 위에 이 래퍼를 씌운다.

커스텀 어댑터가 실제로 구현해야 하는 것(VENDOR_INTEGRATION.md 참고):
- bind_tools(): LangChain 도구 -> OpenAI 호환 tool schema 변환
- 메시지 변환 양방향: LangChain BaseMessage <-> 벤더 포맷(role 매핑,
  tool_calls 구조 변환, tool_call_id 처리)
- _generate/_agenerate: 동기/비동기 진입점
"""
import asyncio
import json
import os
from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from .runpod import RunPodBackend

_ROLE_MAP = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}


def _to_runpod_messages(messages: list[BaseMessage]) -> list[dict]:
    """LangChain 메시지 -> RunPod handler가 받는 OpenAI 호환 dict."""
    result = []
    for m in messages:
        msg: dict = {"role": _ROLE_MAP.get(m.type, "user"), "content": m.content or ""}
        if isinstance(m, AIMessage) and m.tool_calls:
            msg["content"] = m.content or None
            msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["args"], ensure_ascii=False),
                    },
                }
                for tc in m.tool_calls
            ]
        if isinstance(m, ToolMessage):
            msg["tool_call_id"] = m.tool_call_id
        result.append(msg)
    return result


def _build_ai_message(result: dict) -> AIMessage:
    """RunPod handler 응답 dict -> LangChain AIMessage(tool_calls 포함)."""
    raw_tool_calls = result.get("tool_calls") or []
    if raw_tool_calls:
        tool_calls = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except Exception:
                args = {}
            tool_calls.append({
                "id": tc.get("id", ""), "name": fn.get("name", ""),
                "args": args, "type": "tool_call",
            })
        return AIMessage(content=result.get("response") or "", tool_calls=tool_calls)
    return AIMessage(content=result.get("response") or "")


class ChatRunPod(BaseChatModel):
    """RunPod 서버리스 백엔드를 LangChain 인터페이스로 감싼 어댑터."""

    max_tokens: int = 2048
    temperature: float = 0.7

    @property
    def _llm_type(self) -> str:
        return "chat-runpod"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"endpoint_id": os.getenv("RUNPOD_ENDPOINT_ID", "")}

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
        from langchain_core.utils.function_calling import convert_to_openai_tool
        tool_defs = [convert_to_openai_tool(t) for t in tools]
        return self.bind(tools=tool_defs, **kwargs)

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: Any = None, **kwargs: Any,
    ) -> ChatResult:
        result = asyncio.run(self._call_backend(messages, stop=stop, **kwargs))
        return ChatResult(generations=[ChatGeneration(message=_build_ai_message(result))])

    async def _agenerate(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: Any = None, **kwargs: Any,
    ) -> ChatResult:
        result = await self._call_backend(messages, stop=stop, **kwargs)
        return ChatResult(generations=[ChatGeneration(message=_build_ai_message(result))])

    async def _call_backend(self, messages: list[BaseMessage], **kwargs: Any) -> dict:
        backend = RunPodBackend()
        return await backend.generate_chat(
            _to_runpod_messages(messages),
            max_tokens=kwargs.pop("max_tokens", self.max_tokens),
            temperature=kwargs.pop("temperature", self.temperature),
            **kwargs,
        )
