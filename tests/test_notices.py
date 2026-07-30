"""라이브 공지 조회(Phase 12a) 테스트 — NoticeSource 추상화 + 활성 판정.

전부 monkeypatch/stub — 네트워크 호출 없음. check_live_notices 도구 자체의 테스트는
reply/tools.py 변경과 함께 커밋된다(다음 커밋). 노션 어댑터는 12c 범위라 여기서
테스트하지 않는다.
"""
import datetime

import pytest

from app.common.mcp.notices import NoticeSource, get_notice_source, is_notice_active
from app.common.mcp.notices.backends import stub as notice_stub
from app.common.mcp.notices.backends.noop import NoopNoticeSource
from app.common.mcp.notices.backends.stub import StubNoticeSource


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

