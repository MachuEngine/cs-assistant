"""읽기 전용 라이브 공지 소스 인터페이스 (Phase 12a, DESIGN.md 3.2·6.2절).

`EscalationNotifier`(`app/common/mcp/base.py`)를 상속하지 않는다 — 계약이
정반대이기 때문이다.

**fail-fast가 이 인터페이스의 계약이다.** `EscalationNotifier`는 알림(쓰기·
부작용)이라 실패를 삼켜도 안전하지만(외부로 나가는 게 없으니 실패해도 상태가
틀어지지 않는다), 공지 조회는 초안의 내용을 바꾸는 근거다. 조회 실패를 조용한
빈 리스트로 반환하면 "지금 활성 공지가 없음"과 "조회가 실패해서 모름"이
구분되지 않고, 에이전트는 후자를 전자로 오인해 낡은 정책 문서만으로 확답을
써버린다 — 이게 침묵하는 실패보다 나쁜 이유는, 실패가 아예 보이지 않는다는
점이다. 그래서 구현체는 조회가 실패하면 `NoticeLookupError`(혹은 그 하위
예외)를 그대로 던진다. 호출부(`app/modules/reply/tools.py`의
`check_live_notices`)가 이 예외를 잡아 `ctx["notice_lookup_failed"]`를 세우고,
이는 다시 필수 인텐트에 한해 E9 에스컬레이션으로 이어진다
(`app/modules/reply/routing.py`의 `NOTICE_REQUIRED`).
"""
from abc import ABC, abstractmethod


class NoticeLookupError(RuntimeError):
    """공지 조회 실패 — 호출부로 그대로 전파돼야 한다(빈 리스트로 숨기지 않는다)."""


class NoticeSource(ABC):
    """운영자가 작성한 라이브 공지를 조회하는 읽기 전용·멱등 소스.

    반환 형태는 백엔드(노션이든 stub이든)와 무관하게 이 ABC가 고정한다 —
    이 경계 덕분에 12c의 실제 노션 응답 형식이 코어(게이트·활성 판정·라우팅)에
    번지지 않는다:

        [{
            "notice_id": str,       # 백엔드 고유 식별자(노션이면 페이지 ID)
            "title": str,
            "body": str,            # 영어(DESIGN.md 0절) — 모델 컨텍스트로 들어가는 외부 텍스트
            "scope": list[str],     # 카테고리 11종 중 일부(인텐트 아님)
            "valid_from": str,      # "YYYY-MM-DD"
            "valid_until": str,     # "YYYY-MM-DD" 또는 "" (공란 = 기본 TTL)
            "active": bool,
        }, ...]

    활성 판정(기간·`active` 플래그)은 이 계층의 책임이 아니다 —
    `app/common/mcp/notices/activity.is_notice_active()`가 순수 함수로 한다.
    """

    @abstractmethod
    async def get_active_notices(self) -> list[dict]:
        """정규화된 공지 레코드 전체(활성 여부와 무관하게 원본)를 반환한다.

        [엄수] 조회가 실패하면 빈 리스트를 반환하지 말고 `NoticeLookupError`를
        던진다. 메서드 이름은 역사적으로 "활성 공지"를 뜻하지만, 실제 활성
        필터링은 호출부가 `is_notice_active()`로 한다 — 이 메서드는 원본
        레코드 전체를 돌려준다.
        """
        ...
