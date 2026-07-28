"""RunPod 서버리스 백엔드 — 커스텀 어댑터 경로(DESIGN.md 10절).

RunPod의 handler는 비동기 job queue 프로토콜을 쓴다: POST /run으로 제출하면
job_id만 받고, 완료 여부는 GET /status/{job_id}를 폴링해서 확인해야 한다.
공식 langchain 통합이 없는 이유이자, 이 백엔드가 커스텀 어댑터로 필요한
이유다(반면 Anthropic/OpenAI/Ollama는 단일 동기 요청-응답이라 공식
LangChain 클래스로 충분함).

handler 응답 형식: {"output": {"response": str | None, "tool_calls": list | None}}

[엄수] 제출(/run) 응답을 못 받았다고 재제출하지 않는다 — job이 이미 등록된
상태에서 재제출하면 동일 요청이 중복 실행된다. 대신 명확한 예외로 상위에
알린다(CLAUDE.md — chat_runpod 어댑터 주의사항).
"""
import asyncio
import os

import httpx

from ..base import LLMBackend

_BASE = "https://api.runpod.ai/v2"
_POLL_INTERVAL = 5  # seconds
_MAX_POLL = 60  # 최대 5분 대기


class RunPodBackend(LLMBackend):
    def __init__(self):
        self.api_key = os.getenv("RUNPOD_API_KEY", "")
        self.endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID", "")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _payload(self, messages: list, **kwargs) -> dict:
        payload: dict = {
            "input": {
                "messages": messages,
                "max_tokens": kwargs.get("max_tokens", 256),
                "temperature": kwargs.get("temperature", 0.7),
            }
        }
        if kwargs.get("tools"):
            payload["input"]["tools"] = kwargs["tools"]
        if kwargs.get("stop"):
            payload["input"]["stop"] = kwargs["stop"]
        return payload

    async def _submit_async(self, client: httpx.AsyncClient, messages: list[dict], **kwargs) -> str:
        """POST /run으로 job을 한 번 제출하고 job_id를 반환한다."""
        try:
            response = await client.post(
                f"{_BASE}/{self.endpoint_id}/run",
                headers=self._headers(),
                json=self._payload(messages, **kwargs),
            )
        except (httpx.ReadTimeout, httpx.TimeoutException) as exc:
            raise TimeoutError(
                "RunPod 작업 제출 응답을 받지 못했습니다. 재제출하지 않습니다."
            ) from exc
        response.raise_for_status()
        submission = response.json()
        job_id = submission.get("id")
        if not job_id:
            raise RuntimeError("RunPod 작업 제출 응답에 job id가 없습니다.")
        return job_id

    async def _poll_until_done(self, client: httpx.AsyncClient, job_id: str) -> dict:
        """동일 client와 job_id로 완료될 때까지 상태를 폴링한다."""
        for _ in range(_MAX_POLL):
            await asyncio.sleep(_POLL_INTERVAL)
            status_response = await client.get(
                f"{_BASE}/{self.endpoint_id}/status/{job_id}",
                headers=self._headers(),
            )
            status_response.raise_for_status()
            status_data = status_response.json()
            status = status_data.get("status")
            if status == "COMPLETED":
                output = status_data.get("output")
                if not isinstance(output, dict):
                    raise RuntimeError("RunPod 완료 응답의 output 형식이 올바르지 않습니다.")
                return output
            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"RunPod job {job_id} 상태: {status}")

        raise TimeoutError(f"RunPod job {job_id} 응답 초과 ({_MAX_POLL * _POLL_INTERVAL}s)")

    async def _call_raw(self, messages: list[dict], **kwargs) -> dict:
        if not self.api_key or not self.endpoint_id:
            raise RuntimeError("RUNPOD_API_KEY / RUNPOD_ENDPOINT_ID 환경변수가 설정되지 않았습니다.")
        timeout = httpx.Timeout(35, connect=10, read=35)
        async with httpx.AsyncClient(timeout=timeout) as client:
            job_id = await self._submit_async(client, messages, **kwargs)
            return await self._poll_until_done(client, job_id)

    async def generate(self, messages: list[dict], **kwargs) -> str:
        """텍스트 생성 전용(tool calling 불필요한 경우). 문자열 반환."""
        result = await self._call_raw(messages, **kwargs)
        return result.get("response") or ""

    async def generate_chat(self, messages: list[dict], **kwargs) -> dict:
        """tool calling 포함 생성. {"response": str|None, "tool_calls": list|None} 반환."""
        return await self._call_raw(messages, **kwargs)
