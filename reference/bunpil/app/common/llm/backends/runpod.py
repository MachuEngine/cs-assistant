"""RunPod 서버리스 vLLM 백엔드.

handler.py 응답 형식:
  {"output": {"response": str | None, "tool_calls": list | None}}
비동기 run을 한 번만 제출한 뒤 동일 job id를 status polling한다.



1. /run에 POST 요청 → messages, max_tokens 등을 담아서 job 제출
2. 응답으로 job_id 받음 (submission.get("id"))
3. 폴링 루프 시작: 5초 대기 → /status/{job_id}로 GET 요청 → 상태 확인
4. 이때 사용하는 job_id는 계속 1번에서 받은 그 job_id 하나
5. 상태가 COMPLETED면 → output 반환하고 종료
    상태가 FAILED/CANCELLED면 → 에러
    그 외(IN_QUEUE, IN_PROGRESS 등)면 → 다시 5초 자고 같은 job_id로 재확인

"""
import asyncio
import os

import httpx

from ..base import LLMBackend

_BASE = "https://api.runpod.ai/v2"
_POLL_INTERVAL = 5    # seconds
_MAX_POLL      = 60   # 최대 5분 대기(Caddy read_timeout과 동일)


class RunPodBackend(LLMBackend):
    def __init__(self):
        self.api_key     = os.getenv("RUNPOD_API_KEY", "")
        self.endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID", "")

    # 표준 Bearer 토큰 인증 헤더
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    # RunPod API가 요구하는 형식({"input": {...}})으로 감싸는 함수
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

    # 핵심 로직
    async def _submit_async(
        self,
        client: httpx.AsyncClient,
        messages: list[dict],
        **kwargs,
    ) -> str:
        """POST /run으로 job을 한 번 제출하고 job id를 반환한다."""
        # job 제출: /run 엔드포인트에 POST로 job을 등록함
        try:
            response = await client.post(
                f"{_BASE}/{self.endpoint_id}/run",
                headers=self._headers(),
                json=self._payload(messages, **kwargs),
            )
        except (httpx.ReadTimeout, httpx.TimeoutException) as exc:
            # 제출 응답을 못 받은 상태에서 재제출하면 첫 작업과 중복 실행될 수 있다.
            raise TimeoutError(
                "RunPod 작업 제출 응답을 받지 못했습니다. 재제출하지 않습니다."
            ) from exc
        # HTTP status가 4xx/5xx번대면 예외 발생
        response.raise_for_status()
        # JSON 문자열을 파이썬 dict 객체로 변환
        submission = response.json()
        job_id = submission.get("id")
        if not job_id:
            raise RuntimeError("RunPod 작업 제출 응답에 job id가 없습니다.")
        return job_id

    async def _poll_until_done(
        self,
        client: httpx.AsyncClient,
        job_id: str,
    ) -> dict:
        """동일 client와 job id로 완료될 때까지 상태를 폴링한다."""
        # POLLING 루프
        for _ in range(_MAX_POLL):
            await asyncio.sleep(_POLL_INTERVAL)  # 5초씩 최대 _MAX_POLL번 대기
            # /run으로 받은 동일 job id의 상태를 조회
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
        """단일 HTTP client로 job 제출과 상태 폴링을 오케스트레이션한다."""
        if not self.api_key or not self.endpoint_id:
            raise RuntimeError(
                "RUNPOD_API_KEY / RUNPOD_ENDPOINT_ID 환경변수가 설정되지 않았습니다."
            )
        # job 제출(/run) 요청에 적용되는 타임아웃이며 전체 폴링 시간과는 무관함
        timeout = httpx.Timeout(35, connect=10, read=35)
        async with httpx.AsyncClient(timeout=timeout) as client:
            job_id = await self._submit_async(client, messages, **kwargs)
            return await self._poll_until_done(client, job_id)

    async def generate(self, messages: list[dict], **kwargs) -> str:
        """텍스트 생성 전용 (tool calling 불필요한 경우). 문자열 반환."""
        result = await self._call_raw(messages, **kwargs)
        return result.get("response") or ""

    async def generate_chat(self, messages: list[dict], **kwargs) -> dict:
        """tool calling 포함 생성. {"response": str|None, "tool_calls": list|None} 반환."""
        return await self._call_raw(messages, **kwargs)
