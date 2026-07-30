"""노션 공지 어댑터(Phase 12c) 테스트 — 전부 12b 실측 응답 기반 픽스처.

[엄수] 네트워크 호출 없음. `MCPClient.discover_and_call`을 monkeypatch로 갈아끼워
실제 서버 없이 발견·호출·파싱 전 구간을 검증한다. 픽스처의 도구 목록·응답 모양은
2026-07-31 실측값 그대로다(MCP_INTEGRATION.md 2b절).
"""
import json

import pytest

from app.common.mcp.client import MCPError
from app.common.mcp.notices.backends import notion as notion_backend
from app.common.mcp.notices.backends.notion import (
    NotionNoticeSource,
    build_id_args,
    clear_caches,
    normalize_page,
    select_tool,
)
from app.common.mcp.notices.base import NoticeLookupError

# --- 실측 도구 목록 (2026-07-31, mcp/notion 24개 중 발견에 관여하는 것들) -------
# 쓰기 도구를 일부러 함께 둔다 — 스키마 필터만으로는 걸러지지 않는다는 것이
# 이 어댑터가 쓰기 이름 토큰 배제 단계를 갖는 이유다.
_NOTION_TOOLS = [
    {"name": "API-get-self", "inputSchema": {"properties": {}, "required": []}},
    {"name": "API-post-search", "inputSchema": {
        "properties": {"query": {}, "sort": {}, "filter": {}, "start_cursor": {}, "page_size": {}},
        "required": []}},
    {"name": "API-retrieve-a-database", "inputSchema": {
        "properties": {"database_id": {}}, "required": ["database_id"]}},
    {"name": "API-query-data-source", "inputSchema": {
        "properties": {"data_source_id": {}, "filter_properties": {}, "filter": {},
                       "sorts": {}, "start_cursor": {}, "page_size": {},
                       "archived": {}, "in_trash": {}},
        "required": ["data_source_id"]}},
    {"name": "API-retrieve-a-data-source", "inputSchema": {
        "properties": {"data_source_id": {}}, "required": ["data_source_id"]}},
    # ↓ 쓰기 도구. required가 data_source_id 하나뿐이라 스키마 필터를 통과한다.
    {"name": "API-update-a-data-source", "inputSchema": {
        "properties": {"data_source_id": {}, "title": {}, "description": {}, "properties": {}},
        "required": ["data_source_id"]}},
    {"name": "API-list-data-source-templates", "inputSchema": {
        "properties": {"data_source_id": {}, "start_cursor": {}, "page_size": {}},
        "required": ["data_source_id"]}},
    {"name": "API-delete-a-block", "inputSchema": {
        "properties": {"block_id": {}}, "required": ["block_id"]}},
]

_DATA_SOURCE_ID = "3ad3006a-beb0-80d4-a0f0-000ba75833d3"
_DATABASE_ID = "3ad3006abeb080baa9ccfa53a47f5bd3"


def _db_payload() -> dict:
    return {
        "object": "database",
        "id": _DATABASE_ID,
        "data_sources": [{"id": _DATA_SOURCE_ID, "name": "New database"}],
    }


def _page(notice_id: str, *, title="Shipping delay", body="Deliveries are running late.",
          scope=("DELIVERY",), valid_from="2026-07-31", valid_until="2026-08-10",
          active=True) -> dict:
    """실측 응답의 프로퍼티 모양 그대로(rich text 배열/date 객체/multi_select 배열)."""
    def rt(text):
        return [{"type": "text", "text": {"content": text, "link": None},
                 "annotations": {"bold": False}, "plain_text": text, "href": None}] if text else []

    return {
        "object": "page",
        "id": notice_id,
        "properties": {
            "title": {"id": "title", "type": "title", "title": rt(title)},
            "body": {"id": "OLRN", "type": "rich_text", "rich_text": rt(body)},
            "valid_from": {"id": "aP_N", "type": "date",
                           "date": {"start": valid_from, "end": None, "time_zone": None} if valid_from else None},
            "valid_until": {"id": "JiIG", "type": "date",
                            "date": {"start": valid_until, "end": None, "time_zone": None} if valid_until else None},
            "scope": {"id": "wOPc", "type": "multi_select",
                      "multi_select": [{"id": "x", "name": s, "color": "blue"} for s in scope]},
            "active": {"id": "G%3EVv", "type": "checkbox", "checkbox": active},
        },
    }


def _query_payload(pages, has_more=False) -> dict:
    return {"object": "list", "results": pages, "next_cursor": None,
            "has_more": has_more, "type": "page_or_data_source"}


@pytest.fixture(autouse=True)
def _clear():
    clear_caches()
    yield
    clear_caches()


def _install_fake_mcp(monkeypatch, *, db_payload=None, query_payload=None, is_error=False):
    """discover_and_call을 실제 select/build_args는 그대로 태우면서 가로챈다."""
    calls = []

    async def _fake(self, select, build_args, cached_tool=None):
        tool = cached_tool if cached_tool is not None else select(_NOTION_TOOLS)
        args = build_args(tool)
        calls.append({"tool": tool["name"], "args": args})
        if "database" in tool["name"].lower():
            payload = db_payload if db_payload is not None else _db_payload()
        else:
            payload = query_payload if query_payload is not None else _query_payload([])
        return tool, {"isError": is_error, "content": [json.dumps(payload)]}

    monkeypatch.setattr("app.common.mcp.client.MCPClient.discover_and_call", _fake)
    return calls


def _source() -> NotionNoticeSource:
    return NotionNoticeSource(
        url="http://notion-mcp:3000/mcp", token="t", database_id=_DATABASE_ID
    )


# --- 도구 발견: 쓰기 도구를 절대 고르지 않는다 (핵심 가드레일) ----------------

def test_select_query_tool_picks_read_only_query_tool():
    tool = select_tool(_NOTION_TOOLS, hints=("query", "data", "source"),
                       id_keys=("data_source_id",))
    assert tool["name"] == "API-query-data-source"


def test_select_never_returns_a_write_tool():
    """스키마 필터만으로는 update-a-data-source도 후보로 남는다 — 이름 토큰
    배제가 없으면 '이름을 하드코딩하지 않는다'가 파괴적 도구 선택 경로가 된다."""
    tool = select_tool(_NOTION_TOOLS, hints=("query", "data", "source"),
                       id_keys=("data_source_id",))
    assert "update" not in tool["name"].lower()
    assert "delete" not in tool["name"].lower()


def test_select_db_tool_picks_retrieve_a_database():
    tool = select_tool(_NOTION_TOOLS, hints=("retrieve", "database"),
                       id_keys=("database_id",))
    assert tool["name"] == "API-retrieve-a-database"


def test_select_explicit_name_rejects_write_tool():
    with pytest.raises(MCPError, match="쓰기 도구"):
        select_tool(_NOTION_TOOLS, hints=("query",), id_keys=("data_source_id",),
                    explicit_name="API-update-a-data-source")


def test_select_explicit_name_not_found_raises():
    with pytest.raises(MCPError, match="찾지 못했습니다"):
        select_tool(_NOTION_TOOLS, hints=("query",), id_keys=("data_source_id",),
                    explicit_name="API-does-not-exist")


def test_select_no_candidate_raises():
    with pytest.raises(MCPError):
        select_tool(_NOTION_TOOLS, hints=("nonexistent", "hint"),
                    id_keys=("data_source_id",))


def test_build_id_args_uses_server_schema_keys():
    tool = next(t for t in _NOTION_TOOLS if t["name"] == "API-query-data-source")
    args = build_id_args(tool, _DATA_SOURCE_ID, ("data_source_id",), page_size=100)
    assert args["data_source_id"] == _DATA_SOURCE_ID
    assert args["page_size"] == 100


# --- 정규화 변환 (12b 실측 응답 모양) ----------------------------------------

def test_normalize_page_maps_all_properties():
    result = normalize_page(_page("pg-1"))
    assert result == {
        "notice_id": "pg-1",
        "title": "Shipping delay",
        "body": "Deliveries are running late.",
        "scope": ["DELIVERY"],
        "valid_from": "2026-07-31",
        "valid_until": "2026-08-10",
        "active": True,
    }


def test_normalize_page_joins_split_rich_text():
    """노션은 서식 단위로 rich text를 쪼개 보낸다 — 첫 블록만 읽으면 본문이 잘린다."""
    page = _page("pg-1")
    page["properties"]["body"]["rich_text"] = [
        {"plain_text": "Part one. "}, {"plain_text": "Part two."},
    ]
    assert normalize_page(page)["body"] == "Part one. Part two."


def test_normalize_page_empty_valid_until_becomes_empty_string():
    result = normalize_page(_page("pg-1", valid_until=""))
    assert result["valid_until"] == ""


def test_normalize_page_truncates_datetime_to_date():
    page = _page("pg-1")
    page["properties"]["valid_from"]["date"]["start"] = "2026-07-31T10:00:00.000+09:00"
    assert normalize_page(page)["valid_from"] == "2026-07-31"


def test_normalize_page_allows_empty_placeholder_row():
    """노션에서 DB를 만들면 기본으로 생기는 빈 행(전 필드 공란 + active 미체크)이
    실제로 존재한다(실측). active=False면 날짜가 결과에 영향을 줄 수 없으므로
    이 행 하나 때문에 조회 전체가 실패하면 안 된다 — 배송 티켓이 전부 E9로 뒤집힌다."""
    result = normalize_page(
        _page("pg-empty", title="", body="", scope=(), valid_from="", valid_until="", active=False)
    )
    assert result["active"] is False
    assert result["valid_from"] == ""


def test_normalize_page_active_without_valid_from_is_fail_fast():
    """활성인데 시작일이 없으면 유효 기간을 판정할 수 없다 — 조용히 넘기지 않는다."""
    with pytest.raises(NoticeLookupError, match="valid_from"):
        normalize_page(_page("pg-1", valid_from="", active=True))


def test_normalize_page_missing_property_is_fail_fast():
    page = _page("pg-1")
    del page["properties"]["scope"]
    with pytest.raises(NoticeLookupError, match="필수 프로퍼티"):
        normalize_page(page)


# --- 조회 흐름 (2단계 + 캐시) ------------------------------------------------

@pytest.mark.asyncio
async def test_get_active_notices_two_step_lookup(monkeypatch):
    calls = _install_fake_mcp(monkeypatch, query_payload=_query_payload([_page("pg-1")]))
    notices = await _source().get_active_notices()

    assert [c["tool"] for c in calls] == ["API-retrieve-a-database", "API-query-data-source"]
    assert calls[0]["args"] == {"database_id": _DATABASE_ID}
    assert calls[1]["args"]["data_source_id"] == _DATA_SOURCE_ID
    assert len(notices) == 1 and notices[0]["notice_id"] == "pg-1"


@pytest.mark.asyncio
async def test_data_source_id_is_cached_across_calls(monkeypatch):
    calls = _install_fake_mcp(monkeypatch, query_payload=_query_payload([_page("pg-1")]))
    source = _source()
    await source.get_active_notices()
    await source.get_active_notices()

    # 2회차는 database 조회를 건너뛴다 — 루프 안 도구라 왕복 1회가 예산에 직결된다
    assert [c["tool"] for c in calls].count("API-retrieve-a-database") == 1
    assert [c["tool"] for c in calls].count("API-query-data-source") == 2


@pytest.mark.asyncio
async def test_empty_result_returns_empty_list_not_error(monkeypatch):
    _install_fake_mcp(monkeypatch, query_payload=_query_payload([]))
    assert await _source().get_active_notices() == []


# --- fail-fast 계약 ----------------------------------------------------------

@pytest.mark.asyncio
async def test_notion_api_error_payload_is_fail_fast(monkeypatch):
    """[실측] 노션 서버는 object_not_found에도 isError=False로 응답하고 본문에만
    에러를 담는다 — 프로토콜 레벨만 보면 조용히 빈 결과가 된다."""
    _install_fake_mcp(monkeypatch, db_payload={
        "object": "error", "code": "object_not_found",
        "message": "Could not find database", "status": 404,
    })
    with pytest.raises(NoticeLookupError, match="object_not_found"):
        await _source().get_active_notices()


@pytest.mark.asyncio
async def test_tool_level_error_is_fail_fast(monkeypatch):
    _install_fake_mcp(monkeypatch, is_error=True)
    with pytest.raises(NoticeLookupError):
        await _source().get_active_notices()


@pytest.mark.asyncio
async def test_missing_data_sources_is_fail_fast(monkeypatch):
    _install_fake_mcp(monkeypatch, db_payload={"object": "database", "data_sources": []})
    with pytest.raises(NoticeLookupError, match="data_source"):
        await _source().get_active_notices()


@pytest.mark.asyncio
async def test_has_more_is_fail_fast_not_silent_truncation(monkeypatch):
    """활성 공지를 놓치면 게이트⑥이 반영 누락을 못 잡는다 — 조용한 절단 금지."""
    _install_fake_mcp(monkeypatch,
                      query_payload=_query_payload([_page("pg-1")], has_more=True))
    with pytest.raises(NoticeLookupError, match="넘어"):
        await _source().get_active_notices()


@pytest.mark.asyncio
async def test_unconfigured_source_is_fail_fast_not_empty_list():
    """설정 누락과 '공지 없음'은 다르다 — 기능을 끄려면 NOTICE_SOURCE=noop을 쓴다."""
    source = NotionNoticeSource(url="", token="", database_id="")
    with pytest.raises(NoticeLookupError, match="설정되지 않았습니다"):
        await source.get_active_notices()


@pytest.mark.asyncio
async def test_transport_exception_becomes_notice_lookup_error(monkeypatch):
    async def _boom(self, select, build_args, cached_tool=None):
        raise MCPError("connection refused")

    monkeypatch.setattr("app.common.mcp.client.MCPClient.discover_and_call", _boom)
    with pytest.raises(NoticeLookupError, match="통신 실패"):
        await _source().get_active_notices()


@pytest.mark.asyncio
async def test_error_message_never_leaks_notice_body(monkeypatch):
    """오류 메시지에 공지 본문이 섞이면 하드룰 4(로그 PII 금지) 위반 경로가 된다."""
    _install_fake_mcp(monkeypatch, db_payload={
        "object": "error", "code": "unauthorized",
        "message": "SECRET-BODY-TEXT-DO-NOT-LEAK", "status": 401,
    })
    with pytest.raises(NoticeLookupError) as exc_info:
        await _source().get_active_notices()
    assert "SECRET-BODY-TEXT-DO-NOT-LEAK" not in str(exc_info.value)


# --- 팩토리 배선 -------------------------------------------------------------

def test_factory_returns_notion_source(monkeypatch):
    from app.common.mcp.notices.factory import get_notice_source

    monkeypatch.setenv("NOTICE_SOURCE", "notion")
    assert isinstance(get_notice_source(), NotionNoticeSource)


def test_notion_source_reads_config_from_env(monkeypatch):
    monkeypatch.setenv("NOTION_MCP_URL", "http://example:3000/mcp")
    monkeypatch.setenv("NOTION_MCP_TOKEN", "tok")
    monkeypatch.setenv("NOTICE_DB_ID", "db-1")
    monkeypatch.setenv("NOTICE_MCP_TIMEOUT", "12")
    source = NotionNoticeSource()
    assert source.url == "http://example:3000/mcp"
    assert source.database_id == "db-1"
    assert source.timeout == 12.0


def test_notion_backend_is_not_imported_for_noop():
    """지연 import 확인 — noop만 쓰는 배포가 어댑터를 로드하지 않아야 한다."""
    import inspect

    from app.common.mcp.notices import factory

    source_code = inspect.getsource(factory)
    assert "from .backends.notion import" in source_code
    # 모듈 최상단이 아니라 함수 안에서 import 되는지
    assert not source_code.split("def get_notice_source")[0].count("backends.notion")


@pytest.mark.asyncio
async def test_never_calls_a_write_tool_end_to_end(monkeypatch):
    """조회 전 구간에서 실제로 호출된 도구가 전부 읽기 전용인지 확인한다.

    실측 24개 도구 전체(쓰기 포함)를 서버가 준 것처럼 넘겨도, 발견 로직이
    고른 도구만 호출돼야 한다. 이 어댑터가 노션에 쓰기를 하는 경로는 없다.
    """
    write_tools = [
        {"name": "API-post-page", "inputSchema": {
            "properties": {"parent": {}, "properties": {}}, "required": ["parent", "properties"]}},
        {"name": "API-patch-page", "inputSchema": {
            "properties": {"page_id": {}}, "required": ["page_id"]}},
        {"name": "API-update-a-block", "inputSchema": {
            "properties": {"block_id": {}}, "required": ["block_id"]}},
    ]
    full_tool_list = _NOTION_TOOLS + write_tools
    called = []

    async def _fake(self, select, build_args, cached_tool=None):
        tool = cached_tool if cached_tool is not None else select(full_tool_list)
        called.append(tool["name"])
        build_args(tool)
        payload = (_db_payload() if "database" in tool["name"].lower()
                   else _query_payload([_page("pg-1")]))
        return tool, {"isError": False, "content": [json.dumps(payload)]}

    monkeypatch.setattr("app.common.mcp.client.MCPClient.discover_and_call", _fake)
    await _source().get_active_notices()

    assert called, "도구가 하나도 호출되지 않았다"
    for name in called:
        assert notion_backend._is_read_only(name), f"쓰기 도구를 호출했다: {name}"


def test_describe_exception_unwraps_exception_group():
    """anyio/MCP SDK가 전송 오류를 ExceptionGroup으로 감싸 올려 원인 타입이
    가려지던 문제(실측 2026-07-31) — 안쪽까지 풀어야 진단이 된다."""
    inner = ConnectionRefusedError("connection refused")
    group = ExceptionGroup("transport failed", [inner])
    described = notion_backend._describe_exception(group)
    assert "ConnectionRefusedError" in described


def test_describe_exception_never_includes_exception_message():
    """예외 메시지에 공지 본문이 섞여 올라올 여지를 원천 차단한다(하드룰 4)."""
    exc = RuntimeError("SECRET-BODY-TEXT-DO-NOT-LEAK")
    assert "SECRET" not in notion_backend._describe_exception(exc)
    assert notion_backend._describe_exception(exc) == "RuntimeError"


# --- 리뷰 지적 재현: 타입 드리프트 · 탈출구 (2026-07-31) ---------------------

def test_normalize_page_rejects_property_type_drift():
    """이름은 맞는데 타입이 바뀐 경우(checkbox → select 등)를 조용히 넘기면,
    '활성 공지 0건'으로 보이면서 기능만 죽는다 — 이름 누락은 fail-fast인데
    타입 변경은 통과하는 비대칭을 없앤다."""
    page = _page("pg-1")
    page["properties"]["active"] = {"id": "x", "type": "select",
                                    "select": {"name": "yes"}}
    with pytest.raises(NoticeLookupError, match="타입"):
        normalize_page(page)


def test_normalize_page_rejects_scope_type_drift():
    page = _page("pg-1")
    page["properties"]["scope"] = {"id": "x", "type": "rich_text", "rich_text": []}
    with pytest.raises(NoticeLookupError, match="타입"):
        normalize_page(page)


def test_explicit_name_allows_read_tool_with_ambiguous_post_verb():
    """구세대 노션 서버의 조회 도구는 `API-post-database-query`처럼 HTTP 동사
    때문에 이름에 post가 들어간다. 자동 발견이 이걸 배제하는 건 보수적으로
    맞지만, **명시 지정 탈출구까지 막히면 회복 수단이 없어진다**(필수 인텐트
    7종이 전부 E9로 뒤집힌다)."""
    legacy_tools = [
        {"name": "API-post-database-query", "inputSchema": {
            "properties": {"database_id": {}, "page_size": {}}, "required": ["database_id"]}},
        {"name": "API-update-a-data-source", "inputSchema": {
            "properties": {"data_source_id": {}}, "required": ["data_source_id"]}},
    ]
    tool = select_tool(legacy_tools, hints=("query",), id_keys=("database_id",),
                       explicit_name="API-post-database-query")
    assert tool["name"] == "API-post-database-query"


def test_explicit_name_still_rejects_destructive_tool():
    """탈출구를 열어줘도 명백한 파괴적 동사는 계속 막아야 한다."""
    for name in ("API-update-a-data-source", "API-delete-a-block"):
        with pytest.raises(MCPError, match="쓰기 도구"):
            select_tool(_NOTION_TOOLS, hints=("x",), id_keys=("data_source_id",),
                        explicit_name=name)


def test_auto_discovery_still_excludes_ambiguous_post_verb():
    """자동 발견은 보수적으로 유지 — post가 들어간 이름은 후보에서 뺀다."""
    legacy_tools = [
        {"name": "API-post-database-query", "inputSchema": {
            "properties": {"database_id": {}}, "required": ["database_id"]}},
    ]
    with pytest.raises(MCPError):
        select_tool(legacy_tools, hints=("query",), id_keys=("database_id",))
