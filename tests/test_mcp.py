"""MCP 연동(Slack 에스컬레이션 알림) 테스트 — Phase 11.

전부 monkeypatch — 네트워크 호출 없음. 실제 Slack 발송은 이미 사람이 컨테이너로
검증했다(MCP_INTEGRATION.md 2절 "실측 검증 현황") — 여기서는 재시도하지 않는다.
"""
import inspect

import pytest

from app.common.mcp import MCPError, get_notifier
from app.common.mcp.backends.noop import NoopNotifier
from app.common.mcp.backends.slack import (
    SlackNotifier,
    build_arguments,
    build_escalation_message,
    clear_tool_cache,
    select_post_tool,
)
from app.common.mcp.base import EscalationNotifier

# 실측된 zencoderai/slack-mcp 도구 스키마(MCP_INTEGRATION.md 4.3절)
_ZENCODERAI_TOOLS = [
    {"name": "slack_list_channels", "inputSchema": {"properties": {"limit": {}, "cursor": {}}, "required": []}},
    {
        "name": "slack_post_message",
        "inputSchema": {"properties": {"channel_id": {}, "text": {}}, "required": ["channel_id", "text"]},
    },
    {
        "name": "slack_reply_to_thread",
        "inputSchema": {
            "properties": {"channel_id": {}, "thread_ts": {}, "text": {}},
            "required": ["channel_id", "thread_ts", "text"],
        },
    },
    {
        "name": "slack_add_reaction",
        "inputSchema": {
            "properties": {"channel_id": {}, "timestamp": {}, "reaction": {}},
            "required": ["channel_id", "timestamp", "reaction"],
        },
    },
]


# --- factory ------------------------------------------------------------

def test_get_notifier_defaults_to_noop():
    assert isinstance(get_notifier(), NoopNotifier)


def test_get_notifier_switches_to_slack(monkeypatch):
    monkeypatch.setenv("MCP_NOTIFIER", "slack")
    assert isinstance(get_notifier(), SlackNotifier)


def test_get_notifier_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("MCP_NOTIFIER", "teams")
    with pytest.raises(NotImplementedError):
        get_notifier()


@pytest.mark.asyncio
async def test_noop_notifier_returns_false():
    result = await NoopNotifier().notify_escalation(
        ticket_id="REQ-1", ticket_ref="", intent="cancel_order",
        category="ORDER", confidence=0.9, escalation_reason="E1",
    )
    assert result is False


# --- 도구 발견/선택 (MCP_INTEGRATION.md 4.3절 알고리즘) ----------------------

def test_select_post_tool_picks_schema_and_name_match():
    tool = select_post_tool(_ZENCODERAI_TOOLS)
    assert tool["name"] == "slack_post_message"


def test_select_post_tool_filters_out_tool_with_unfillable_required_arg():
    """실측 근거(4.3절): 스키마 필터만으로는 slack_reply_to_thread도 후보로 남는데,
    thread_ts를 채울 수 없어 걸러져야 한다."""
    only_thread_reply = [_ZENCODERAI_TOOLS[2]]  # slack_reply_to_thread만
    with pytest.raises(MCPError):
        select_post_tool(only_thread_reply)


def test_select_post_tool_finds_tool_with_unconventional_name():
    """실측 사례: korotovsky의 발송 도구명은 conversations_add_message라
    이름 힌트(post/send+message)에 전혀 안 걸린다 — 스키마 모양으로 잡혀야 한다."""
    tools = [{
        "name": "conversations_add_message",
        "inputSchema": {"properties": {"channel": {}, "text": {}}, "required": ["channel", "text"]},
    }]
    tool = select_post_tool(tools)
    assert tool["name"] == "conversations_add_message"


def test_select_post_tool_explicit_name_override():
    tool = select_post_tool(_ZENCODERAI_TOOLS, explicit_name="slack_reply_to_thread")
    assert tool["name"] == "slack_reply_to_thread"


def test_select_post_tool_explicit_name_not_found_raises():
    with pytest.raises(MCPError, match="찾지 못했습니다"):
        select_post_tool(_ZENCODERAI_TOOLS, explicit_name="does_not_exist")


def test_select_post_tool_no_candidates_raises():
    tools = [{"name": "slack_list_channels", "inputSchema": {"properties": {"limit": {}}, "required": []}}]
    with pytest.raises(MCPError):
        select_post_tool(tools)


def test_build_arguments_maps_to_schema_keys():
    tool = _ZENCODERAI_TOOLS[1]  # slack_post_message
    args = build_arguments(tool, channel="C123", text="hello")
    assert args == {"channel_id": "C123", "text": "hello"}


# --- 페이로드 가드레일 (핵심 — MCP_INTEGRATION.md 검증계획 2번) --------------

def test_notify_escalation_signature_cannot_carry_ticket_body_or_customer_id():
    """EscalationNotifier.notify_escalation()이 받는 인자 이름 자체를 검사한다.
    dict나 ticket 객체를 통째로 넘기는 시그니처였다면 본문·customer_id가 실수로
    섞여 들어갈 길이 있다 — 키워드 인자를 개별로 강제하는 게 구조적 방어다."""
    params = set(inspect.signature(EscalationNotifier.notify_escalation).parameters)
    assert "customer_id" not in params
    assert "ticket_text" not in params
    assert "draft" not in params
    assert "ticket" not in params  # 통째로 넘기는 dict 인자 금지
    assert params == {
        "self", "ticket_id", "ticket_ref", "intent", "category", "confidence", "escalation_reason",
    }


def test_build_escalation_message_excludes_pii_and_only_has_allowed_fields():
    msg = build_escalation_message(
        ticket_id="REQ-abcdef123456",
        ticket_ref="ZENDESK-9987",
        intent="cancel_order",
        category="ORDER",
        confidence=0.42,
        escalation_reason="E6",
    )
    # 허용된 것들은 있어야 한다
    assert "REQ-abcdef123456" in msg
    assert "ZENDESK-9987" in msg
    assert "cancel_order" in msg
    assert "E6" in msg
    assert "referenced order could not be found" in msg  # routing.ESCALATION_REASONS 재사용
    # 금지된 것들은 이 함수의 인자 자체에 없어 넣을 수가 없다는 걸
    # 명시적으로 재확인 — 실수로 파라미터가 늘어나는 회귀를 잡는다
    params = set(inspect.signature(build_escalation_message).parameters)
    assert params == {
        "ticket_id", "ticket_ref", "intent", "category", "confidence", "escalation_reason",
    }


def test_build_escalation_message_reuses_routing_reason_descriptions():
    """E1~E8 설명 단일 출처 확인 — routing.py와 다른 문구를 새로 안 만든다."""
    from app.modules.reply.routing import ESCALATION_REASONS

    for code, desc in ESCALATION_REASONS.items():
        msg = build_escalation_message(
            ticket_id="REQ-x", ticket_ref="", intent="x", category="X",
            confidence=0.5, escalation_reason=code,
        )
        assert desc in msg


# --- SlackNotifier 통합 (discover_and_call은 monkeypatch로 대체) -------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_cache()
    yield
    clear_tool_cache()


@pytest.mark.asyncio
async def test_slack_notifier_returns_false_when_unconfigured():
    notifier = SlackNotifier(url="", token="", channel="")
    result = await notifier.notify_escalation(
        ticket_id="REQ-1", ticket_ref="", intent="cancel_order",
        category="ORDER", confidence=0.9, escalation_reason="E1",
    )
    assert result is False


@pytest.mark.asyncio
async def test_slack_notifier_fail_soft_on_client_exception(monkeypatch):
    """알림 실패가 예외로 새지 않는다(base.py 계약) — /reply의 outcome=failed로
    뒤집히면 안 되는 게 이 설계에서 가장 피해야 할 실패 모드(3.5절)."""
    notifier = SlackNotifier(url="http://unreachable:1234/mcp", token="", channel="C1")

    async def _boom(self, select, build_args, cached_tool=None):
        raise RuntimeError("network exploded")

    monkeypatch.setattr("app.common.mcp.client.MCPClient.discover_and_call", _boom)

    result = await notifier.notify_escalation(
        ticket_id="REQ-1", ticket_ref="", intent="cancel_order",
        category="ORDER", confidence=0.9, escalation_reason="E1",
    )
    assert result is False  # 예외가 여기서 멈췄다 — 위로 전파 안 됨


@pytest.mark.asyncio
async def test_slack_notifier_success_path_and_caching(monkeypatch):
    calls = []

    async def _fake_discover_and_call(self, select, build_args, cached_tool=None):
        calls.append(cached_tool)
        if cached_tool is None:
            tool = select(_ZENCODERAI_TOOLS)
        else:
            tool = cached_tool
        args = build_args(tool)
        assert args["channel_id"] == "C1"
        return tool, {"isError": False, "content": ["ok"]}

    monkeypatch.setattr("app.common.mcp.client.MCPClient.discover_and_call", _fake_discover_and_call)

    notifier = SlackNotifier(url="http://fake/mcp", token="", channel="C1")

    ok1 = await notifier.notify_escalation(
        ticket_id="REQ-1", ticket_ref="", intent="cancel_order",
        category="ORDER", confidence=0.9, escalation_reason="E1",
    )
    ok2 = await notifier.notify_escalation(
        ticket_id="REQ-2", ticket_ref="", intent="cancel_order",
        category="ORDER", confidence=0.9, escalation_reason="E1",
    )

    assert ok1 is True and ok2 is True
    # 1차 호출은 캐시가 없어 select()를 거쳤고(cached_tool=None), 2차는 캐시를 씀
    assert calls[0] is None
    assert calls[1] is not None and calls[1]["name"] == "slack_post_message"


@pytest.mark.asyncio
async def test_slack_notifier_returns_false_on_tool_error(monkeypatch):
    async def _fake_discover_and_call(self, select, build_args, cached_tool=None):
        tool = {"name": "slack_post_message", "inputSchema": {}}
        return tool, {"isError": True, "content": ["not_in_channel"]}

    monkeypatch.setattr("app.common.mcp.client.MCPClient.discover_and_call", _fake_discover_and_call)

    notifier = SlackNotifier(url="http://fake/mcp", token="", channel="C1")
    result = await notifier.notify_escalation(
        ticket_id="REQ-1", ticket_ref="", intent="cancel_order",
        category="ORDER", confidence=0.9, escalation_reason="E1",
    )
    assert result is False


def test_blank_notifier_falls_back_to_noop(monkeypatch):
    """`MCP_NOTIFIER=` 처럼 값 없이 키만 둔 .env를 미설정과 같이 본다
    (notices/factory.py와 같은 처리 — .env.example의 빈 값 스타일 때문에 흔하다)."""
    monkeypatch.setenv("MCP_NOTIFIER", "")
    assert isinstance(get_notifier(), NoopNotifier)


def test_misspelled_notifier_still_fails_loudly(monkeypatch):
    monkeypatch.setenv("MCP_NOTIFIER", "Slack")  # 대소문자 오타
    with pytest.raises(NotImplementedError):
        get_notifier()
