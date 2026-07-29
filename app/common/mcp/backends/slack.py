"""Slack 에스컬레이션 알림 — MCP 표준 흐름(tools/list 발견 → tools/call).

**도구 이름을 코드에 박지 않는다.** 서버에 tools/list를 물어 어떤 도구가 있는지
발견하고, 서버가 준 inputSchema에 맞춰 인자를 구성한다(MCP_INTEGRATION.md 4.3절).
"Slack MCP 서버"는 공식 단일 구현이 없고 커뮤니티 구현마다 도구 이름
(slack_post_message / conversations_add_message)과 인자 키(channel_id / channel)가
달라서, 발견 없이는 특정 구현에 종속된다(2026-07-29 조사 — korotovsky의 발송
도구명은 conversations_add_message라 이름 힌트에 전혀 안 걸린다).

자동 선택이 잘못 고르는 경우를 위해 SLACK_MCP_TOOL_NAME으로 명시 지정할 수 있다 —
이건 발견을 건너뛰는 게 아니라, 발견된 목록 안에서 어느 것을 쓸지 못 박는
탈출구다(스키마는 여전히 서버가 준 것을 쓴다).

보안: 이 모듈이 만드는 메시지에는 **티켓 본문·초안·customer_id가 절대 들어가지
않는다**(CLAUDE.md 하드룰 3 유지 — 사용자 결정, 2026-07-29). 외부로 나가는 것은
식별자와 분류 메타데이터뿐이고, 상담원은 ticket_ref를 통해 원래 CS 시스템에서
본문을 본다. tests/test_mcp.py가 이걸 단정한다.
"""
import datetime
import logging
import os

from app.modules.reply.routing import ESCALATION_REASONS

from ..base import EscalationNotifier
from ..client import MCPClient, MCPError

logger = logging.getLogger(__name__)

# 도구 선택 휴리스틱 — 이름 힌트(모든 단어가 이름에 포함돼야 함), 우선순위 순
_TOOL_NAME_HINTS = (
    ("post", "message"),
    ("send", "message"),
    ("create", "message"),
    ("post", "chat"),
)
# 인자 키 후보(우선순위 순) — 서버 스키마의 실제 property 이름과 맞춘다
_CHANNEL_KEYS = ("channel_id", "channel", "channel_name", "conversation_id", "to")
_TEXT_KEYS = ("text", "message", "content", "body", "markdown_text")

# tools/list 결과(로 발견해 선택까지 마친 도구 정의) 캐시. 서버 URL별로 둔다.
# MCP_INTEGRATION.md 4.4절 — 세션이 아니라 순수 성능 최적화라 프로세스별로
# 들고 있어도 무방하다(캐시가 비어도 다시 조회할 뿐 동작은 같다).
_tool_cache: dict[str, dict] = {}


def clear_tool_cache() -> None:
    """테스트·설정 변경용."""
    _tool_cache.clear()


def build_escalation_message(
    *,
    ticket_id: str,
    ticket_ref: str,
    intent: str,
    category: str,
    confidence: float,
    escalation_reason: str,
) -> str:
    """Slack에 보낼 본문을 만든다(순수 함수 — 가드레일 테스트 대상).

    E1~E8 설명은 routing.py의 ESCALATION_REASONS를 그대로 재사용한다. 여기서
    한글 라벨을 새로 만들면 에스컬레이션 사유의 단일 출처가 둘로 갈라진다.
    """
    reason_desc = ESCALATION_REASONS.get(escalation_reason, "unknown reason")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    lines = [
        ":rotating_light: *상담원 확인이 필요한 티켓입니다*",
        f"• 사유: `{escalation_reason or 'UNKNOWN'}` — {reason_desc}",
        f"• 인텐트: `{intent}` / 카테고리: `{category}`",
        f"• 분류 확신도: {confidence:.2f}",
    ]
    if ticket_ref:
        lines.append(f"• 티켓: {ticket_ref}")
    lines.append(f"• 요청 ID: `{ticket_id}`")
    lines.append(f"• 발생 시각: {now}")
    lines.append("_티켓 본문과 초안은 이 알림에 포함되지 않습니다 — CS 시스템에서 확인하세요._")
    return "\n".join(lines)


def _properties(tool: dict) -> dict:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _find_key(props: dict, candidates: tuple[str, ...]) -> str | None:
    """스키마 property 중 candidates에 해당하는 키를 찾는다(정확 일치 우선)."""
    lowered = {name.lower(): name for name in props}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    # 부분 일치 폴백 — "slack_channel" 같은 접두/접미 변형 대응
    for candidate in candidates:
        for name_lower, original in lowered.items():
            if candidate in name_lower:
                return original
    return None


def _has_channel_and_text_keys(tool: dict) -> bool:
    """스키마 모양으로 '메시지 발송' 후보인지 판별한다(이름보다 신뢰도 높음)."""
    props = _properties(tool)
    return bool(_find_key(props, _CHANNEL_KEYS) and _find_key(props, _TEXT_KEYS))


def _can_fill_required(tool: dict) -> bool:
    """required 인자를 채널/텍스트만으로 전부 채울 수 있는지 확인한다.

    실측(2026-07-29): 스키마 필터만으로는 slack_post_message(channel_id, text)와
    slack_reply_to_thread(channel_id, thread_ts, text) 둘 다 후보로 남는데,
    후자는 thread_ts를 채울 수 없다. 이걸 걸러내지 않으면 이름 힌트가 우연히
    안 맞는 서버에서 필수 인자 누락으로 호출이 실패한다.
    """
    props = _properties(tool)
    channel_key = _find_key(props, _CHANNEL_KEYS)
    text_key = _find_key(props, _TEXT_KEYS)
    fillable = {k for k in (channel_key, text_key) if k}
    required = set((tool.get("inputSchema") or {}).get("required") or [])
    return required <= fillable


def _name_hint_score(name: str) -> int:
    lowered = name.lower()
    for index, hints in enumerate(_TOOL_NAME_HINTS):
        if all(hint in lowered for hint in hints):
            return len(_TOOL_NAME_HINTS) - index
    return 0


def select_post_tool(tools: list[dict], explicit_name: str = "") -> dict:
    """발견된 도구 목록에서 메시지 발송 도구를 고른다(MCP_INTEGRATION.md 4.3절 알고리즘).

    1. explicit_name이 있으면 그 이름을 목록에서 찾는다(없으면 에러 — 조용히
       다른 도구로 대체하지 않는다)
    2. 아니면 스키마 모양(채널류+텍스트류 키 보유)으로 후보를 거른다
    3. required 인자를 전부 채울 수 있는 도구만 남긴다
    4. 그래도 여럿이면 이름 힌트로 순위
    5. 후보가 없으면 사용 가능한 도구 목록과 함께 에러
    """
    if explicit_name:
        for tool in tools:
            if tool.get("name") == explicit_name:
                return tool
        available = ", ".join(sorted(t.get("name", "?") for t in tools))
        raise MCPError(
            f"SLACK_MCP_TOOL_NAME='{explicit_name}' 도구를 서버에서 찾지 못했습니다. "
            f"사용 가능한 도구: {available}"
        )

    candidates = [t for t in tools if _has_channel_and_text_keys(t)]
    candidates = [t for t in candidates if _can_fill_required(t)]
    if not candidates:
        available = ", ".join(sorted(t.get("name", "?") for t in tools))
        raise MCPError(
            "채널+텍스트만으로 필수 인자를 채울 수 있는 메시지 발송 도구를 찾지 못했습니다. "
            f"SLACK_MCP_TOOL_NAME으로 직접 지정하세요. 사용 가능한 도구: {available}"
        )

    candidates.sort(key=lambda t: _name_hint_score(t.get("name", "")), reverse=True)
    return candidates[0]


def build_arguments(tool: dict, channel: str, text: str) -> dict:
    """서버가 준 inputSchema에 맞춰 tools/call 인자를 만든다.

    select_post_tool()이 이미 required 인자를 채울 수 있는 도구만 통과시키므로,
    여기서 또 실패하는 경우는 논리적으로 없다 — 그래도 방어적으로 재확인한다.
    """
    props = _properties(tool)
    channel_key = _find_key(props, _CHANNEL_KEYS)
    text_key = _find_key(props, _TEXT_KEYS)
    if not channel_key or not text_key:
        raise MCPError(
            f"도구 '{tool.get('name')}'의 스키마에서 채널/텍스트 인자를 찾지 못했습니다: "
            f"{sorted(props)}"
        )
    return {channel_key: channel, text_key: text}


class SlackNotifier(EscalationNotifier):
    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        channel: str | None = None,
        tool_name: str | None = None,
        timeout: float | None = None,
    ):
        self.url = url if url is not None else os.getenv("SLACK_MCP_URL", "")
        self.token = token if token is not None else os.getenv("SLACK_MCP_TOKEN", "")
        self.channel = channel if channel is not None else os.getenv("SLACK_ESCALATION_CHANNEL", "")
        self.tool_name = tool_name if tool_name is not None else os.getenv("SLACK_MCP_TOOL_NAME", "")
        self.timeout = timeout if timeout is not None else float(os.getenv("MCP_NOTIFY_TIMEOUT", "5"))

    def _client(self) -> MCPClient:
        return MCPClient(url=self.url, token=self.token, timeout=self.timeout)

    def _select(self, tools: list[dict]) -> dict:
        return select_post_tool(tools, self.tool_name)

    def _build_args(self, tool: dict, text: str) -> dict:
        return build_arguments(tool, self.channel, text)

    async def notify_escalation(
        self,
        *,
        ticket_id: str,
        ticket_ref: str,
        intent: str,
        category: str,
        confidence: float,
        escalation_reason: str,
    ) -> bool:
        # [엄수] 이 메서드는 예외를 밖으로 던지지 않는다(base.py 계약).
        # /reply는 전체를 try/except로 감싸 예외 시 outcome=failed를 반환하므로,
        # 여기서 새어나가면 Slack 장애가 멀쩡한 에스컬레이션 결과를 뒤집는다.
        try:
            if not self.url or not self.channel:
                logger.warning("SLACK_MCP_URL/SLACK_ESCALATION_CHANNEL 미설정 — 알림을 건너뜁니다.")
                return False

            text = build_escalation_message(
                ticket_id=ticket_id,
                ticket_ref=ticket_ref,
                intent=intent,
                category=category,
                confidence=confidence,
                escalation_reason=escalation_reason,
            )

            client = self._client()
            cached = _tool_cache.get(self.url)
            tool, result = await client.discover_and_call(
                select=self._select,
                build_args=lambda tool: self._build_args(tool, text),
                cached_tool=cached,
            )
            if cached is None:
                _tool_cache[self.url] = tool

            if result["isError"]:
                logger.warning("Slack 알림 전송 실패(도구 오류): %s", result["content"])
                return False

            logger.info("에스컬레이션 알림 전송 완료 (사유 %s)", escalation_reason)
            return True
        except Exception:
            # 티켓 본문·식별자를 로그에 남기지 않는다(하드룰 4).
            logger.warning("에스컬레이션 알림 전송 실패 — 파이프라인은 계속합니다.", exc_info=True)
            return False
