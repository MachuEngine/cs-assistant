"""LangChain BaseChatModel adapter for RunPod serverless vLLM.

LangGraph ReAct 에이전트는 LangChain 인터페이스가 필요하므로
RunPodBackend 위에 래퍼를 씌운다.
tool_calls 파싱: handler가 반환한 OpenAI 호환 tool_calls 구조체를
LangChain AIMessage.tool_calls 형식으로 변환한다.
"""
import asyncio
import json
import os
from typing import Any, List, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from .runpod import RunPodBackend # 현재 패키지의 runpod.py에서 실제 RunPod 통신을 담당하는 클래스를 가져옵니다.

"""
* BaseMessage: 모든 메세지의 공통 부모
---
* AIMessage: AI의 텍스트 응답 또는 도구 호출 
AIMessage(
    content="",
    tool_calls=[
        {
            "id": "call_1",
            "name": "get_weather",
            "args": {"city": "서울"},
        }
    ],
)
---
* ToolMessage: Tool 응답을 AI에 전달
ToolMessage(
    content="서울은 맑음",
    tool_call_id="call_1",
)

"""

"""
LangChain 채팅 모델의 표준 반환 형식
ChatResult(
    generations=[
        ChatGeneration(
            message=AIMessage(content="안녕하세요")
        )
    ]
)
"""

def _to_runpod_messages(messages: List[BaseMessage]) -> List[dict]:
    """
    -> LangChain to RunPod
    이 함수는 LangChain 메시지 객체를 RunPod API가 받는 딕셔너리 형태로 변환합니다.
        입력: 
        [
            HumanMessage(content="안녕하세요"),
            AIMessage(content="무엇을 도와드릴까요?"),
        ]
        출력:
        [
            {"role": "user", "content": "안녕하세요"},
            {"role": "assistant", "content": "무엇을 도와드릴까요?"},
        ]
    """

    """
    역할 매핑: LangChain과 OpenAI 호환 API는 서로 역할 이름의 일부가 상이하여 이를 매핑 시켜야한다.
    | LangChain `m.type` | OpenAI 호환 역할 |
    | ------------------ | ------------ |
    | `human`            | `user`       |
    | `ai`               | `assistant`  |
    | `system`           | `system`     |
    | `tool`             | `tool`       |
    """
    role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    result = []
    for m in messages:
        msg: dict = {"role": role_map.get(m.type, "user"), "content": m.content or ""}
        if isinstance(m, AIMessage) and m.tool_calls:
            msg["content"] = m.content or None
            # 아래는 LangChain 도구 호출을 OpenAI 형식으로 변환하는 부분
            """
            { // LangChain 형식
                "id": "call_abc",
                "name": "get_weather",
                "args": {
                    "city": "서울"
                },
                "type": "tool_call",
            }

            { // OpenAI 호환 형식
                "id": "call_abc",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": "{\"city\": \"서울\"}"    <- dict이 아니라 JSON임 
                }
            }
            
            """
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
    """handler 응답 dict → LangChain AIMessage (tool_calls 포함).
    -> RunPod to LangChain
    이 함수는 RunPod API가 받는 딕셔너리 형태를 LangChain 메시지 객체 형태로 변환합니다.

    { // 일반 텍스트 응답
        "response": "안녕하세요."
    }

    { // 도구 호출 응답
        "response": "",
        "tool_calls": [
            {
                "id": "call_abc",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": "{\"city\": \"서울\"}"
                }
            }
        ]
    }
    """
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
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "args": args,
                "type": "tool_call",
            })
        return AIMessage(content=result.get("response") or "", tool_calls=tool_calls)
    return AIMessage(content=result.get("response") or "") # 도구 호출이 없으면 일반 텍스트 응답으로 변환 


class ChatRunPod(BaseChatModel):
    """RunPod 서버리스 vLLM 백엔드를 LangChain 인터페이스로 감싼 어댑터."""

    max_tokens: int = 2048 # 모델이 생성할 최대 토큰 수 
    temperature: float = 0.7 # 응답의 무작위성을 조절 - 0.0에 가까울 수록 일관되고 결정적인 응답. 값이 높을 수록 더 다양한 표현. (코드 생성이나 도구 호출 -> 일반적으로 낮은 값이 안정적)

    @property
    def _llm_type(self) -> str:
        return "chat-runpod"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"endpoint_id": os.getenv("RUNPOD_ENDPOINT_ID", "")}
    # ㄴ> 현재 모델 인스턴스를 식별하는 설정값을 반환
    # LangChain tracting이나 캐싱에서 서로 다른 모델 설정을 구분하는 데 활용될 수 있음 


    """
        bind_tools()은 아래와 같이 모델이 어떤 도구가 있는지 알 수 있는 JSON 구조로 변환한다.  

        LangChain 또는 LangGraph 도구를 모델에 연결하는 메서드입니다.
        convert_to_openai_tool는 LangChain 도구를 OpenAI 호환 tool schema로 변환합니다.

        eg. 예로 아래와 같은 get_weather 도구가 있다고 가정하면, 
        @tool
        def get_weather(city: str) -> str:
            // 도시의 날씨를 조회한다.
        
        convert_to_openai_tool([get_weather]) 사용 시 아래와 같은 정의가 만들어진다. 

        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "도시의 날씨를 조회한다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string"
                        }
                    },
                    "required": ["city"]
                }
            }
        }
    """
    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
        from langchain_core.utils.function_calling import convert_to_openai_tool
        tool_defs = [convert_to_openai_tool(t) for t in tools]
        return self.bind(tools=tool_defs, **kwargs)

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        result = asyncio.run(self._call_backend(messages, stop=stop, **kwargs))
        return ChatResult(generations=[ChatGeneration(message=_build_ai_message(result))])

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        result = await self._call_backend(messages, stop=stop, **kwargs)
        return ChatResult(generations=[ChatGeneration(message=_build_ai_message(result))])

    async def _call_backend(self, messages: List[BaseMessage], **kwargs: Any) -> dict:
        backend = RunPodBackend()
        return await backend.generate_chat(
            _to_runpod_messages(messages),
            max_tokens=kwargs.pop("max_tokens", self.max_tokens),
            temperature=kwargs.pop("temperature", self.temperature),
            **kwargs,
        )
