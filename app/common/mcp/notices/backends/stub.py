"""테스트·eval 전용 공지 소스 — 프로세스 안에서 레코드/실패를 주입한다.

모듈 레벨 상태로 둔다(인스턴스 상태가 아니다) — `get_notice_source()`는 호출할
때마다 새 인스턴스를 만들므로, 인스턴스 속성에 주입한 값은 다음 호출에서
사라진다. `app/common/mcp/backends/slack.py`의 모듈 레벨 `_tool_cache`와
같은 이유다.

eval 러너는 골든 행마다 다른 공지 집합(혹은 조회 실패)을 주입해야 하므로,
반드시 각 케이스 전에 `reset()`을 호출해 이전 케이스의 상태가 새지 않게 한다.
"""
from .. import base

_records: list[dict] = []
_failure: Exception | None = None


def set_notices(records: list[dict]) -> None:
    global _records
    _records = records


def set_failure(exc: Exception | None) -> None:
    global _failure
    _failure = exc


def reset() -> None:
    global _records, _failure
    _records = []
    _failure = None


class StubNoticeSource(base.NoticeSource):
    async def get_active_notices(self) -> list[dict]:
        if _failure is not None:
            raise _failure
        return list(_records)
