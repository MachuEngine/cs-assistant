"""알림 비활성 백엔드 — 기본값.

MCP_NOTIFIER를 설정하지 않으면 이 백엔드가 쓰인다. CI·로컬 개발·기존 테스트가
외부 의존 없이 그대로 돌아가야 하기 때문이다(LLM 백엔드가 키 없을 때 fail-fast
하는 것과 다르다 — 알림은 없어도 파이프라인이 성립한다).
"""
import logging

from ..base import EscalationNotifier

logger = logging.getLogger(__name__)


class NoopNotifier(EscalationNotifier):
    async def notify_escalation(
        self,
        *,
        ticket_id: str,
        ticket_ref: str,
        intent: str,
        category: str,
        confidence: float,
        escalation_reason: str,
    ) -> bool:
        # 티켓 본문은 물론 식별자도 로그에 남기지 않는다(CLAUDE.md 하드룰 4).
        logger.debug("알림 백엔드가 비활성(noop)이라 에스컬레이션 알림을 보내지 않습니다.")
        return False
