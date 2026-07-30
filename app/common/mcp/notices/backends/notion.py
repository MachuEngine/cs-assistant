"""노션 공지 소스 — MCP 표준 흐름(tools/list 발견 → tools/call), 읽기 전용.

`app/common/mcp/backends/slack.py`의 발견·선택 패턴을 그대로 따르되, **쓰기 도구
배제 단계가 추가로 필요하다.** Slack MCP 서버는 도구가 8개뿐이고 대부분 읽기였지만,
노션 공식 서버는 REST API를 1:1로 감싼 **24개**를 노출하며 그중 상당수가 쓰기다
(실측 2026-07-31, MCP_INTEGRATION.md 2b절). 스키마 필터만 쓰면
`API-update-a-data-source`(required=`data_source_id`)도 후보로 남아,
"이름을 하드코딩하지 않는다"는 원칙이 오히려 **파괴적 도구를 고르는 경로**가 된다.
그래서 발견 → **쓰기 이름 토큰 배제** → 스키마 필터 → 이름 힌트 순으로 거른다.

조회는 **2단계**다(2025-09-03+ 노션 API가 database와 data_source를 분리했다):
    retrieve-a-database(database_id) → data_sources[0].id → query-data-source(...)
`database_id`만으로는 행을 가져올 수 없다 — 12b 실측으로 확인했고, 설계 시점에는
없던 제약이다. `data_source_id`는 거의 바뀌지 않으므로 캐시해 정상 경로에서는
호출 1회로 줄인다.

**fail-fast가 계약이다**(`notices/base.py`). 조회 실패·파싱 실패를 빈 리스트로
삼키지 않고 `NoticeLookupError`로 올린다 — 알림(Slack, fail-soft)과 의도적으로
반대다. 자세한 이유는 `notices/base.py` docstring 참고.
"""
import json
import logging
import os

from ...client import MCPClient, MCPError
from ...toolschema import find_key as _find_key
from ...toolschema import properties as _properties
from ..base import NoticeLookupError, NoticeSource

logger = logging.getLogger(__name__)

# 쓰기 도구 배제용 이름 토큰. **두 단계로 나눈다.**
#
# _DESTRUCTIVE_TOKENS: 명백히 상태를 바꾸는 동사. 자동 발견이든 명시 지정이든
#   무조건 막는다.
# _AMBIGUOUS_TOKENS: 쓰기처럼 보이지만 읽기일 수 있는 것. "post"가 대표적이다 —
#   구세대 노션 서버의 조회 도구 이름이 `API-post-database-query`(HTTP POST로
#   질의하는 읽기 연산)다. 자동 발견에서는 보수적으로 배제하되,
#   **명시 지정(탈출구)에서는 허용**한다. 둘 다 막으면 자동 발견이 실패했을 때
#   회복 수단이 없어져 필수 인텐트가 전부 E9로 뒤집힌다.
_DESTRUCTIVE_TOKENS = (
    "update", "patch", "create", "delete", "move", "append",
    "duplicate", "restore", "trash", "archive", "insert", "replace",
)
_AMBIGUOUS_TOKENS = ("post",)

# 이름 힌트 — 후보 이름에 **모든** 토큰이 들어가야 한다(slack.py와 같은 방식).
_DB_TOOL_HINTS = ("retrieve", "database")
_QUERY_TOOL_HINTS = ("query", "data", "source")

_DB_ID_KEYS = ("database_id", "database", "id")
_DS_ID_KEYS = ("data_source_id", "data_source", "id")

# 노션 페이지 프로퍼티 이름 → 기대 타입. 사람이 노션 UI에서 만든 스키마와
# 일치해야 한다(PROMPTS.md Phase 12 "사전 확정 사항"의 DB 스키마).
#
# **타입까지 검사하는 이유**: 이름만 보면, 운영자가 노션에서 컬럼 타입을 바꿨을 때
# (`active`를 checkbox → select 등) `.get("checkbox")`가 None을 반환해 **모든 공지가
# 조용히 비활성**이 된다. `scope` 타입이 바뀌면 어떤 공지도 grounded로 승격되지 않는다.
# 둘 다 예외 없이 "활성 공지 0건"으로 보여서, 이름을 지웠을 때는 즉시 터지는데
# 타입을 바꿨을 때는 기능만 조용히 죽는 비대칭이 생긴다 — 이 모듈의 나머지가
# 지키는 "조용한 절단 금지" 원칙과 어긋난다.
_REQUIRED_PROPS = {
    "title": "title",
    "body": "rich_text",
    "valid_from": "date",
    "valid_until": "date",
    "scope": "multi_select",
    "active": "checkbox",
}

# 한 번에 가져올 행 수. 노션 API 상한이 100이고, 그보다 많으면 has_more로
# 판단해 fail-fast 한다(아래 참고) — 조용한 절단을 만들지 않는다.
_PAGE_SIZE = 100

# 발견한 도구 정의 캐시(서버 URL + 용도별), data_source_id 캐시(URL + DB별).
# slack.py의 _tool_cache와 같은 이유 — 순수 성능 최적화라 비어 있어도 동작은 같다.
_tool_cache: dict[str, dict] = {}
_data_source_cache: dict[str, str] = {}


def clear_caches() -> None:
    """테스트·설정 변경용."""
    _tool_cache.clear()
    _data_source_cache.clear()


def _describe_exception(exc: BaseException, _depth: int = 0) -> str:
    """예외를 진단 가능한 짧은 문자열로 만든다 — 타입 이름만, 메시지는 넣지 않는다.

    anyio/MCP SDK는 전송 오류를 `ExceptionGroup`으로 감싸 올리기 때문에
    `type(exc).__name__`만 쓰면 "ExceptionGroup"이 되어 원인 진단이 불가능하다
    (실측 2026-07-31: 닫힌 포트로 붙였을 때 실제로 이렇게 나왔다). 안쪽 예외까지
    풀어서 실제 원인 타입을 보여준다.

    [엄수] 예외 **메시지**는 넣지 않는다 — 상위 계층에서 공지 본문이 섞여
    들어올 여지를 원천 차단한다(하드룰 4).
    """
    name = type(exc).__name__
    inner = getattr(exc, "exceptions", None)
    if inner and _depth < 3:
        causes = ", ".join(_describe_exception(e, _depth + 1) for e in inner[:3])
        return f"{name}({causes})"
    return name


def _is_destructive(name: str) -> bool:
    """명백한 쓰기 동사 — 어떤 경로로도 선택되면 안 된다."""
    lowered = name.lower()
    return any(token in lowered for token in _DESTRUCTIVE_TOKENS)


def _is_read_only(name: str) -> bool:
    """자동 발견용 보수적 판정 — 애매한 것(post)도 배제한다."""
    lowered = name.lower()
    return not _is_destructive(name) and not any(
        token in lowered for token in _AMBIGUOUS_TOKENS
    )


def select_tool(
    tools: list[dict],
    *,
    hints: tuple[str, ...],
    id_keys: tuple[str, ...],
    explicit_name: str = "",
) -> dict:
    """발견된 도구 목록에서 읽기 전용 조회 도구를 고른다.

    1. explicit_name이 있으면 목록에서 그 이름을 찾는다(없으면 에러 — 조용히
       다른 도구로 대체하지 않는다). 발견을 건너뛰는 게 아니라, 발견된 목록
       안에서 어느 것을 쓸지 못 박는 탈출구다.
    2. **쓰기 이름 토큰이 들어간 도구를 먼저 배제한다**(이 어댑터 고유 단계).
    3. 식별자 인자를 채울 수 있고, required를 그것만으로 전부 채울 수 있는
       도구만 남긴다.
    4. 이름 힌트(모든 토큰 포함)로 최종 선택한다.
    """
    if explicit_name:
        for tool in tools:
            if tool.get("name") == explicit_name:
                # 명시 지정은 사람이 책임지고 고른 것이므로 애매한 이름(post 등)은
                # 허용하되, 명백한 파괴적 동사는 여전히 막는다.
                if _is_destructive(explicit_name):
                    raise MCPError(
                        f"'{explicit_name}'은 쓰기 도구로 보입니다. 공지 조회는 "
                        "읽기 전용이어야 합니다."
                    )
                return tool
        available = ", ".join(sorted(t.get("name", "?") for t in tools))
        raise MCPError(
            f"지정한 도구 '{explicit_name}'을 서버에서 찾지 못했습니다. "
            f"사용 가능한 도구: {available}"
        )

    candidates = [t for t in tools if _is_read_only(t.get("name", ""))]

    fillable = []
    for tool in candidates:
        id_key = _find_key(_properties(tool), id_keys)
        if not id_key:
            continue
        required = set((tool.get("inputSchema") or {}).get("required") or [])
        if required <= {id_key}:
            fillable.append(tool)

    matched = [
        t for t in fillable
        if all(hint in t.get("name", "").lower() for hint in hints)
    ]
    if not matched:
        available = ", ".join(sorted(t.get("name", "?") for t in fillable))
        raise MCPError(
            f"이름 힌트 {hints}에 맞는 읽기 전용 조회 도구를 찾지 못했습니다. "
            f"후보: {available}"
        )
    return matched[0]


def build_id_args(tool: dict, value: str, id_keys: tuple[str, ...], *, page_size: int = 0) -> dict:
    """서버가 준 inputSchema에 맞춰 tools/call 인자를 만든다."""
    props = _properties(tool)
    id_key = _find_key(props, id_keys)
    if not id_key:
        raise MCPError(
            f"도구 '{tool.get('name')}'의 스키마에서 식별자 인자를 찾지 못했습니다: "
            f"{sorted(props)}"
        )
    args = {id_key: value}
    if page_size:
        size_key = _find_key(props, ("page_size",))
        if size_key:
            args[size_key] = page_size
    return args


def _payload(result: dict, what: str) -> dict:
    """tools/call 결과에서 JSON 페이로드를 꺼낸다.

    [실측 2026-07-31] 노션 서버는 `object_not_found` 같은 API 오류에도
    **isError=False**로 응답하고 본문에만 `{"object": "error", ...}`를 담는다.
    프로토콜 레벨 오류만 보고 성공으로 판단하면 조용히 빈 결과를 만들게 되므로
    페이로드까지 반드시 확인한다.
    """
    if result.get("isError"):
        raise NoticeLookupError(f"{what} 호출이 도구 오류로 실패했습니다.")

    content = result.get("content") or []
    if not content:
        raise NoticeLookupError(f"{what} 응답이 비어 있습니다.")

    try:
        payload = json.loads(content[0])
    except (json.JSONDecodeError, TypeError) as exc:
        raise NoticeLookupError(f"{what} 응답을 JSON으로 파싱하지 못했습니다.") from exc

    if payload.get("object") == "error":
        # 메시지에 공지 본문이 섞일 여지가 없는 필드만 남긴다(하드룰 4).
        raise NoticeLookupError(
            f"{what} 실패 — 노션 API 오류 code={payload.get('code')}"
        )
    return payload


def _rich_text(value: dict, key: str) -> str:
    """title/rich_text 배열을 평문으로 합친다(노션이 서식 단위로 쪼개 보낸다)."""
    blocks = value.get(key) or []
    parts = []
    for block in blocks:
        text = block.get("plain_text")
        if text is None:
            text = (block.get("text") or {}).get("content") or ""
        parts.append(text)
    return "".join(parts)


def _date_start(value: dict) -> str:
    """노션 date 프로퍼티 → "YYYY-MM-DD". 값이 없으면 빈 문자열.

    시각이 포함된 경우(`2026-07-31T10:00:00+09:00`) 날짜 부분만 남긴다 —
    12a의 `is_notice_active()`는 날짜 단위로만 판정한다(UTC 기준).
    """
    date_obj = value.get("date")
    if not isinstance(date_obj, dict):
        return ""
    start = date_obj.get("start") or ""
    return start.split("T")[0]


def normalize_page(page: dict) -> dict:
    """노션 페이지 1건 → `NoticeSource`가 고정한 정규화 형태.

    필수 프로퍼티가 없으면 fail-fast 한다(스키마가 어긋난 상태를 조용히
    넘기면 공지가 통째로 누락된 채 초안이 나간다).

    단, **`active=false`인 행은 `valid_from`이 비어 있어도 허용한다.**
    `is_notice_active()`가 `active`를 먼저 보고 단락 평가하므로 날짜가
    결과에 영향을 줄 수 없고, 노션에서 DB를 만들면 기본으로 생기는 **빈
    플레이스홀더 행**(전 필드 공란 + active 미체크)이 실제로 존재하기
    때문이다(실측 2026-07-31). 이 행 때문에 조회 전체가 실패하면 배송 계열
    티켓이 전부 E9로 뒤집힌다 — 스키마 위반이 아니라 "아직 안 쓴 행"이다.
    `active=true`인데 `valid_from`이 없으면 활성 기간을 판정할 수 없으므로
    그때는 fail-fast 한다.
    """
    props = page.get("properties") or {}
    missing = [name for name in _REQUIRED_PROPS if name not in props]
    if missing:
        raise NoticeLookupError(
            f"공지 페이지({page.get('id')})에 필수 프로퍼티가 없습니다: {missing}. "
            "노션 DB 스키마를 확인하세요."
        )

    mistyped = [
        f"{name}(기대 {expected}, 실제 {props[name].get('type')})"
        for name, expected in _REQUIRED_PROPS.items()
        if props[name].get("type") != expected
    ]
    if mistyped:
        raise NoticeLookupError(
            f"공지 페이지({page.get('id')})의 프로퍼티 타입이 다릅니다: {mistyped}. "
            "노션 DB 스키마를 확인하세요."
        )

    active = bool(props["active"].get("checkbox"))
    valid_from = _date_start(props["valid_from"])
    if active and not valid_from:
        raise NoticeLookupError(
            f"활성 공지({page.get('id')})에 valid_from이 없어 유효 기간을 "
            "판정할 수 없습니다."
        )

    return {
        "notice_id": page.get("id", ""),
        "title": _rich_text(props["title"], "title"),
        "body": _rich_text(props["body"], "rich_text"),
        "scope": [
            opt.get("name", "")
            for opt in (props["scope"].get("multi_select") or [])
            if opt.get("name")
        ],
        "valid_from": valid_from,
        "valid_until": _date_start(props["valid_until"]),
        "active": active,
    }


class NotionNoticeSource(NoticeSource):
    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        database_id: str | None = None,
        timeout: float | None = None,
        db_tool_name: str | None = None,
        query_tool_name: str | None = None,
    ):
        self.url = url if url is not None else os.getenv("NOTION_MCP_URL", "")
        self.token = token if token is not None else os.getenv("NOTION_MCP_TOKEN", "")
        self.database_id = (
            database_id if database_id is not None else os.getenv("NOTICE_DB_ID", "")
        )
        self.timeout = (
            timeout if timeout is not None else float(os.getenv("NOTICE_MCP_TIMEOUT", "8"))
        )
        self.db_tool_name = (
            db_tool_name if db_tool_name is not None else os.getenv("NOTION_DB_TOOL_NAME", "")
        )
        self.query_tool_name = (
            query_tool_name if query_tool_name is not None
            else os.getenv("NOTION_QUERY_TOOL_NAME", "")
        )

    def _client(self) -> MCPClient:
        return MCPClient(url=self.url, token=self.token, timeout=self.timeout)

    async def _resolve_data_source_id(self, client: MCPClient) -> str:
        cache_key = f"{self.url}:{self.database_id}"
        cached = _data_source_cache.get(cache_key)
        if cached:
            return cached

        tool_key = f"{self.url}:database"
        tool, result = await client.discover_and_call(
            select=lambda tools: select_tool(
                tools, hints=_DB_TOOL_HINTS, id_keys=_DB_ID_KEYS,
                explicit_name=self.db_tool_name,
            ),
            build_args=lambda t: build_id_args(t, self.database_id, _DB_ID_KEYS),
            cached_tool=_tool_cache.get(tool_key),
        )
        _tool_cache.setdefault(tool_key, tool)

        payload = _payload(result, "데이터베이스 조회")
        data_sources = payload.get("data_sources") or []
        if not data_sources:
            raise NoticeLookupError(
                "노션 데이터베이스에 data_source가 없습니다. NOTICE_DB_ID가 "
                "데이터베이스 ID(뷰 ID가 아니라)인지, 통합에 공유됐는지 확인하세요."
            )

        data_source_id = data_sources[0].get("id") or ""
        if not data_source_id:
            raise NoticeLookupError("data_source 항목에 id가 없습니다.")
        _data_source_cache[cache_key] = data_source_id
        return data_source_id

    async def get_active_notices(self) -> list[dict]:
        if not self.url or not self.database_id:
            # [엄수] 빈 리스트로 넘기지 않는다 — 설정 누락과 "공지 없음"은 다르다.
            # (기능을 끄고 싶으면 NOTICE_SOURCE=noop을 쓴다.)
            raise NoticeLookupError(
                "NOTION_MCP_URL 또는 NOTICE_DB_ID가 설정되지 않았습니다."
            )

        try:
            client = self._client()
            data_source_id = await self._resolve_data_source_id(client)

            tool_key = f"{self.url}:query"
            tool, result = await client.discover_and_call(
                select=lambda tools: select_tool(
                    tools, hints=_QUERY_TOOL_HINTS, id_keys=_DS_ID_KEYS,
                    explicit_name=self.query_tool_name,
                ),
                build_args=lambda t: build_id_args(
                    t, data_source_id, _DS_ID_KEYS, page_size=_PAGE_SIZE
                ),
                cached_tool=_tool_cache.get(tool_key),
            )
            _tool_cache.setdefault(tool_key, tool)

            payload = _payload(result, "공지 조회")
        except NoticeLookupError:
            raise
        except MCPError as exc:
            raise NoticeLookupError(f"노션 MCP 통신 실패: {exc}") from exc
        except Exception as exc:  # 예상 못 한 오류도 계약대로 fail-fast로 올린다
            raise NoticeLookupError(
                f"노션 공지 조회 중 오류: {_describe_exception(exc)}"
            ) from exc

        if payload.get("has_more"):
            # 조용한 절단을 만들지 않는다 — 활성 공지를 놓치면 게이트 ⑥이
            # 반영 누락을 못 잡고 낡은 기대치가 그대로 나간다.
            raise NoticeLookupError(
                f"공지가 {_PAGE_SIZE}건을 넘어 일부만 조회됐습니다. 오래된 공지를 "
                "정리하거나 페이지네이션을 구현해야 합니다."
            )

        results = payload.get("results") or []
        notices = [normalize_page(page) for page in results]
        logger.debug("노션 공지 %d건 조회 완료", len(notices))
        return notices
