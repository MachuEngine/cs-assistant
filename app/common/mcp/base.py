"""MCP 연동 인터페이스 — 에스컬레이션 알림(DESIGN.md 10절, MCP_INTEGRATION.md).

`app/common/llm/base.py`와 같은 역할이다: 파이프라인 코드가 특정 벤더
(Slack/Teams/...)를 직접 알지 못하게 막는 추상 경계.

**fail-soft가 이 인터페이스의 계약이다.** LLM 백엔드(fail-fast)와 의도적으로
반대다 — 생성 실패는 숨기면 안 되지만, 알림 실패 때문에 멀쩡한 에스컬레이션
판정이 outcome=failed로 뒤바뀌면 안 된다(MCP_INTEGRATION.md 3.2·3.5절).
구현체는 예외를 밖으로 던지지 않고 내부에서 로깅한 뒤 False를 반환한다.
"""
from abc import ABC, abstractmethod


class EscalationNotifier(ABC):
    """에스컬레이션이 확정됐을 때 사람에게 알리는 채널.

    인자를 dict가 아니라 **키워드 개별 인자로 받는 것이 의도된 안전장치**다 —
    ReplyState나 ticket dict를 통째로 넘길 수 없으므로, 티켓 본문·초안이
    실수로 외부 채널에 섞여 나가는 경로가 구조적으로 막힌다
    (CLAUDE.md 보안 하드룰 3: 사용자 입력 비저장).
    """

    @abstractmethod
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
        """알림을 보낸다. 성공하면 True, 실패하거나 비활성이면 False.

        [엄수] 어떤 경우에도 예외를 밖으로 던지지 않는다.
        """
        ...
