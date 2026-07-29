"""MCP 클라이언트 — 공식 `mcp` SDK를 얇게 감싼다(MCP_INTEGRATION.md 4.1절).

대상 서버(zencoderai/slack-mcp)가 MCP SDK v1.13.2(구 스펙, stateful — 실측
확인: `initialize` 응답 protocolVersion "2025-03-26" + `mcp-session-id` 헤더
발급, 2026-07-29)로 동작하므로 우리 클라이언트도 v1 라인(`mcp>=1.28,<2`,
requirements.txt)으로 맞춘다.

**세션은 알림 1건 범위에서만 열고 닫는다** — 장기 연결을 유지하지 않는다.
`discover_and_call()` 하나가 `initialize`부터 `tools/call`까지 한 세션 안에서
끝낸다. 이래야 2026-07-28 stateless로 서버가 넘어올 때 `initialize` 단계만
사라지고 나머지는 그대로 남는다(4.1.1절 — SDK 교체 영향 범위를 이 파일
하나로 가두는 것이 이 파일이 SDK를 직접 노출하지 않는 이유이기도 하다).

재시도를 하지 않는다 — 응답을 못 받은 상태에서 재시도하면 이미 발송됐을
수 있는 요청을 중복 실행하게 된다(RunPod 어댑터가 겪은 것과 같은 함정,
app/common/llm/backends/runpod.py 참고).
"""
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError

logger = logging.getLogger(__name__)

SelectFn = Callable[[list[dict]], dict]
BuildArgsFn = Callable[[dict], dict]


class MCPError(RuntimeError):
    """MCP 서버 통신·프로토콜 오류 — SDK 예외(McpError 등)를 우리 타입으로 감싼다."""


class MCPClient:
    """단일 MCP 서버에 대한 얇은 래퍼. 상태를 갖지 않는다 — url/token/timeout만
    들고 있고, 매 호출마다 독립적인 세션을 새로 연다."""

    def __init__(self, url: str, token: str = "", timeout: float = 5.0):
        self.url = url
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict:
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @asynccontextmanager
    async def _session(self):
        if not self.url:
            raise MCPError("MCP 서버 URL이 설정되지 않았습니다.")
        try:
            async with streamablehttp_client(
                self.url, headers=self._headers(), timeout=self.timeout
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
        except McpError as exc:
            raise MCPError(f"MCP 서버 통신 실패: {exc}") from exc

    async def discover_and_call(
        self,
        select: SelectFn,
        build_args: BuildArgsFn,
        cached_tool: dict | None = None,
    ) -> tuple[dict, dict]:
        """도구를 발견(또는 캐시 사용)하고 호출까지 한 세션 안에서 수행한다.

        select/build_args는 도메인 계층(backends/slack.py)이 넘기는 콜백이다 —
        이 클래스는 Slack을 모른다. cached_tool을 넘기면 tools/list를 건너뛰고
        바로 호출한다(4.4절 캐싱 — 캐시가 비어도 동작은 같고 성능 최적화일
        뿐이다).

        반환: (사용한 tool 정의, 호출 결과 {"isError": bool, "content": [str]})
        """
        async with self._session() as session:
            if cached_tool is None:
                tools_result = await session.list_tools()
                tools = [{"name": t.name, "inputSchema": t.inputSchema} for t in tools_result.tools]
                if not tools:
                    raise MCPError("MCP 서버가 도구를 하나도 제공하지 않습니다.")
                tool = select(tools)
            else:
                tool = cached_tool

            arguments = build_args(tool)
            call_result = await session.call_tool(tool["name"], arguments)
            result = {
                "isError": bool(call_result.isError),
                "content": [getattr(c, "text", None) or str(c) for c in (call_result.content or [])],
            }
            return tool, result
