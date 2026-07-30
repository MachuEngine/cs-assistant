"""공지 조회 비활성 백엔드 — 기본값.

NOTICE_SOURCE를 설정하지 않으면 이 백엔드가 쓰인다. 이건 실패가 아니라 기능이
꺼져 있는 상태다 — check_live_notices 도구가 이 상태를 알아채고
(ctx["notice_lookup_failed"]를 세우지 않고) 정상 진행하게 해준다.
"""
from ..base import NoticeSource


class NoopNoticeSource(NoticeSource):
    async def get_active_notices(self) -> list[dict]:
        return []
