import json
import os

import httpx

from ..base import LLMBackend


class OllamaBackend(LLMBackend):
    def __init__(self, model=None):
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:14b-instruct")

    """
    (입력) -> 모델에 입력할 시스템/유저 프롬프트입니다. 
    messages: list[dict] 는 Ollama 채팅 API 형식의 메시지 리스트입니다.
    
        예를 들면, 
        messages = [
            {
                "role": "system",
                "content": "친절하게 설명하세요.",
            },
            {
                "role": "user",
                "content": "파이썬이 무엇인가요?",
            },
        ]
    (출력) -> 모델이 생성한 최종 문자열이 출력입니다. 
    
        str -> "파이썬은 범용 프로그래밍 언어입니다."

    ---
    json=payload

    {
        "model": "qwen2.5:14b-instruct",
        "messages": [
            {
                "role": "user",
                "content": "안녕하세요"
            }
        ],
        "stream": true
    }

    --- 
    async for line in resp.aiter_lines():
    -> 각 반복에서 line에는 JSON 한 줄이 들어옵니다.

    {"message":{"role":"assistant","content":"파이썬"},"done":false}
    {"message":{"role":"assistant","content":"은"},"done":false}
    {"message":{"role":"assistant","content":" 프로그래밍 언어입니다."},"done":false}
    {"message":{"role":"assistant","content":""},"done":true}

    -> async for를 사용하는 이유는 네트워크 응답을 기다리는 동안 다른 비동기 작업이 실행될 수 있도록 하기 위해서입니다.
    """
    async def generate(self, messages: list[dict], **kwargs) -> str:
        # stream=True로 토큰 단위 수신 → CPU 느린 환경에서도 timeout 회피
        payload = {"model": self.model, "messages": messages, "stream": True, **kwargs}
        tokens = [] # 서버가 보내는 응답 조각(LLM 응답 조각)을 순서대로 저장합니다. 
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=300)) as client:
            async with client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line: # 빈 줄 건너뛰기
                        continue
                    chunk = json.loads(line)
                    tokens.append(chunk["message"]["content"]) # content 값만 tokens에 이어붙임.
                    if chunk.get("done"):
                        break
        return "".join(tokens) # "파이썬" + "은" + " 프로그래밍 언어입니다." + ""
