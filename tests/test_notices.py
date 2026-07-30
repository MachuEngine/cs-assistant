"""라이브 공지 조회(Phase 12a) 테스트 — NoticeSource 추상화 + 활성 판정 + 도구.

전부 monkeypatch/stub — 네트워크 호출 없음. 노션 어댑터는 12c 범위라 여기서
테스트하지 않는다.
"""
import datetime

import pytest

from app.common.mcp.notices import NoticeSource, get_notice_source, is_notice_active
from app.common.mcp.notices.backends import stub as notice_stub
from app.common.mcp.notices.backends.noop import NoopNoticeSource
from app.common.mcp.notices.backends.stub import StubNoticeSource
from app.modules.reply import tools as reply_tools


@pytest.fixture(autouse=True)
def _reset_stub():
    notice_stub.reset()
    yield
    notice_stub.reset()


def _record(**overrides) -> dict:
    # 도구 레벨 테스트는 is_notice_active()를 today= 없이(실제 UTC 오늘 기준) 부르므로,
    # 고정된 과거 날짜를 쓰면 테스트 실행 시점에 따라 활성 여부가 바뀐다 — 항상
    # "지금 기준으로 활성"이도록 오늘 날짜를 기준으로 넉넉한 범위를 잡는다.
    today = datetime.datetime.now(datetime.timezone.utc).date()
    base = {
        "notice_id": "N1",
        "title": "Shipping delay",
        "body": "Shipping is delayed by 3 days this week due to weather.",
        "scope": ["DELIVERY"],
        "valid_from": (today - datetime.timedelta(days=1)).isoformat(),
        "valid_until": (today + datetime.timedelta(days=365)).isoformat(),
        "active": True,
    }
    base.update(overrides)
    return base


# --- factory --------------------------------------------------------------

def test_get_notice_source_defaults_to_noop():
    assert isinstance(get_notice_source(), NoopNoticeSource)


def test_get_notice_source_switches_to_stub(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    assert isinstance(get_notice_source(), StubNoticeSource)


def test_get_notice_source_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "notion")
    with pytest.raises(NotImplementedError):
        get_notice_source()


# --- is_notice_active 경계값 ------------------------------------------------

def test_active_within_explicit_range():
    rec = _record(valid_from="2026-07-01", valid_until="2026-07-31")
    assert is_notice_active(rec, today=datetime.date(2026, 7, 15)) is True


def test_active_on_start_day_inclusive():
    rec = _record(valid_from="2026-07-01", valid_until="2026-07-31")
    assert is_notice_active(rec, today=datetime.date(2026, 7, 1)) is True


def test_active_on_end_day_inclusive():
    rec = _record(valid_from="2026-07-01", valid_until="2026-07-31")
    assert is_notice_active(rec, today=datetime.date(2026, 7, 31)) is True


def test_inactive_day_after_end():
    rec = _record(valid_from="2026-07-01", valid_until="2026-07-31")
    assert is_notice_active(rec, today=datetime.date(2026, 8, 1)) is False


def test_inactive_day_before_start():
    rec = _record(valid_from="2026-07-01", valid_until="2026-07-31")
    assert is_notice_active(rec, today=datetime.date(2026, 6, 30)) is False


def test_default_ttl_boundary_active_on_ttl_day(monkeypatch):
    monkeypatch.setenv("NOTICE_DEFAULT_TTL_DAYS", "14")
    rec = _record(valid_from="2026-07-01", valid_until="")
    assert is_notice_active(rec, today=datetime.date(2026, 7, 15)) is True


def test_default_ttl_boundary_inactive_day_after_ttl(monkeypatch):
    monkeypatch.setenv("NOTICE_DEFAULT_TTL_DAYS", "14")
    rec = _record(valid_from="2026-07-01", valid_until="")
    assert is_notice_active(rec, today=datetime.date(2026, 7, 16)) is False


def test_active_false_overrides_valid_date_range():
    rec = _record(valid_from="2026-01-01", valid_until="2099-01-01", active=False)
    assert is_notice_active(rec, today=datetime.date(2026, 7, 15)) is False


# --- noop / stub 백엔드 -----------------------------------------------------

@pytest.mark.asyncio
async def test_noop_notice_source_returns_empty_list():
    assert await NoopNoticeSource().get_active_notices() == []


@pytest.mark.asyncio
async def test_stub_notice_source_returns_injected_records():
    notice_stub.set_notices([_record()])
    assert await StubNoticeSource().get_active_notices() == [_record()]


@pytest.mark.asyncio
async def test_stub_notice_source_raises_injected_failure():
    notice_stub.set_failure(RuntimeError("notion unavailable"))
    with pytest.raises(RuntimeError, match="notion unavailable"):
        await StubNoticeSource().get_active_notices()


@pytest.mark.asyncio
async def test_stub_notice_source_reset_clears_state():
    notice_stub.set_notices([_record()])
    notice_stub.set_failure(RuntimeError("x"))
    notice_stub.reset()
    assert await StubNoticeSource().get_active_notices() == []


def test_notice_source_is_abstract():
    with pytest.raises(TypeError):
        NoticeSource()


# --- check_live_notices 도구 -------------------------------------------------

def _fresh_session(intent: str):
    reply_tools.bind_session()
    reply_tools.init_session(ticket_text="", order_id="", intent=intent)


@pytest.mark.asyncio
async def test_check_live_notices_noop_returns_no_active_notices(monkeypatch):
    monkeypatch.delenv("NOTICE_SOURCE", raising=False)
    _fresh_session(intent="delivery_period")
    result = await reply_tools.check_live_notices.ainvoke({})
    assert result == "No active live notices."
    ctx = reply_tools.get_ctx()
    assert ctx["notices_checked"] is True
    assert ctx["notice_lookup_failed"] is False
    assert ctx["active_notices"] == []
    assert ctx["grounded_notices"] == []


@pytest.mark.asyncio
async def test_check_live_notices_returns_active_scope_agnostic(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    notice_stub.set_notices([
        _record(notice_id="N1", scope=["DELIVERY"]),
        _record(notice_id="N2", scope=["PAYMENT"], title="Card outage", body="Card payments are down."),
    ])
    _fresh_session(intent="delivery_period")  # category DELIVERY
    result = await reply_tools.check_live_notices.ainvoke({})
    assert "N1" in result and "N2" in result  # 반환은 활성 전부(scope 무관)

    ctx = reply_tools.get_ctx()
    assert {n["notice_id"] for n in ctx["active_notices"]} == {"N1", "N2"}
    assert {n["notice_id"] for n in ctx["grounded_notices"]} == {"N1"}  # scope 일치만 grounded


@pytest.mark.asyncio
async def test_check_live_notices_grounded_body_enters_tool_results_log(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    notice_stub.set_notices([
        _record(notice_id="N1", scope=["DELIVERY"], body="Shipping delayed 3 days."),
        _record(notice_id="N2", scope=["PAYMENT"], body="Card outage amount $999 affected."),
    ])
    _fresh_session(intent="delivery_period")
    await reply_tools.check_live_notices.ainvoke({})
    ctx = reply_tools.get_ctx()
    log = " ".join(ctx["tool_results_log"])
    assert "Shipping delayed 3 days." in log
    assert "$999" not in log  # scope 불일치 공지의 본문은 근거로 승격되지 않는다


@pytest.mark.asyncio
async def test_check_live_notices_sets_failure_flag_and_does_not_raise(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    notice_stub.set_failure(RuntimeError("timeout"))
    _fresh_session(intent="delivery_period")
    result = await reply_tools.check_live_notices.ainvoke({})
    assert "failed" in result.lower()
    ctx = reply_tools.get_ctx()
    assert ctx["notice_lookup_failed"] is True


@pytest.mark.asyncio
async def test_check_live_notices_masks_pii_in_body(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    notice_stub.set_notices([
        _record(notice_id="N1", scope=["DELIVERY"], body="Contact ops@northwind.example for details."),
    ])
    _fresh_session(intent="delivery_period")
    result = await reply_tools.check_live_notices.ainvoke({})
    assert "ops@northwind.example" not in result
    assert "{{EMAIL}}" in result


@pytest.mark.asyncio
async def test_check_live_notices_respects_count_and_length_caps(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    monkeypatch.setenv("NOTICE_MAX_COUNT", "1")
    monkeypatch.setenv("NOTICE_MAX_BODY_CHARS", "10")
    notice_stub.set_notices([
        _record(notice_id="N1", scope=["DELIVERY"], body="x" * 50),
        _record(notice_id="N2", scope=["DELIVERY"], body="y" * 50),
    ])
    _fresh_session(intent="delivery_period")
    result = await reply_tools.check_live_notices.ainvoke({})
    assert "N2" not in result  # 두 번째 공지는 건수 상한에 걸려 반환 문자열에서 잘림
    assert "xxxxxxxxxx... [truncated]" in result
