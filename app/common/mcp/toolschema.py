"""MCP 도구 스키마 조회 헬퍼 — 백엔드들이 공유한다.

`tools/list`가 준 도구 정의에서 property를 꺼내고, 우리가 채우려는 인자에
해당하는 **서버 쪽 키 이름**을 찾는다. 도구 이름을 하드코딩하지 않듯 인자 키도
하드코딩하지 않기 위한 최소 유틸이다(MCP_INTEGRATION.md 4.3절).

Slack(알림)과 Notion(공지) 백엔드가 글자 그대로 같은 구현을 각자 들고 있었는데,
`find_key()`의 부분 일치 폴백이 미묘해서 한쪽만 고치면 다른 쪽에 버그가 남는
형태였다(2026-07-31 리뷰 지적) — 그래서 여기로 모았다.

**도구 선택 정책은 여기로 모으지 않는다.** 두 백엔드의 선택 규칙이 근본적으로
다르기 때문이다 — Slack은 "채널+텍스트 스키마 + 이름 힌트 점수 정렬", Notion은
"쓰기 동사 배제 + 힌트 전량 일치". 억지로 합치면 인자만 늘고 어느 정책이 어느
백엔드에 적용되는지 추적이 어려워진다.
"""


def properties(tool: dict) -> dict:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def find_key(props: dict, candidates: tuple[str, ...]) -> str | None:
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
