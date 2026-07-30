"""공지 활성 판정 — 순수 함수, 날짜 비교를 LLM에 맡기지 않는다(PROMPTS.md Phase 12).

UTC 기준 / valid_from <= today <= valid_until 양쪽 포함 / valid_until 공란은
valid_from + NOTICE_DEFAULT_TTL_DAYS로 취급 / active=False는 기간과 무관하게
항상 비활성.
"""
import datetime
import os


def is_notice_active(notice: dict, *, today: datetime.date | None = None) -> bool:
    """`notice`(NoticeSource가 반환한 정규화된 레코드 1건)가 오늘 활성인지 판정한다.

    `today`를 명시하면 그 날짜 기준으로 판정한다(경계값 테스트를 결정론적으로
    만들기 위한 훅 — 실제 호출부는 생략해 UTC 오늘 날짜를 쓴다).
    """
    if not notice.get("active", True):
        return False

    if today is None:
        today = datetime.datetime.now(datetime.timezone.utc).date()

    valid_from = datetime.date.fromisoformat(notice["valid_from"])
    raw_until = notice.get("valid_until") or ""
    if raw_until:
        valid_until = datetime.date.fromisoformat(raw_until)
    else:
        ttl_days = int(os.getenv("NOTICE_DEFAULT_TTL_DAYS", "14"))
        valid_until = valid_from + datetime.timedelta(days=ttl_days)

    return valid_from <= today <= valid_until
