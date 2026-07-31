"""알림 백엔드 진입점 — MCP_NOTIFIER 환경변수로 전환.

`app/common/llm/factory.py`와 같은 패턴이되 기본값이 다르다: LLM은 백엔드가
반드시 있어야 하지만 알림은 없어도 파이프라인이 성립하므로 기본값이 noop다
(MCP_INTEGRATION.md 3.2절 — 의도적 비대칭).

파이프라인 코드는 이 팩토리를 경유하고 SlackNotifier를 직접 import 하지 않는다.
"""
import os

from .backends.noop import NoopNotifier
from .base import EscalationNotifier


def get_notifier() -> EscalationNotifier:
    # 빈 값(`MCP_NOTIFIER=` 만 있는 .env)은 미설정과 같이 본다 — notices/factory.py와
    # 같은 이유다. 오타는 그대로 실패시킨다.
    backend = os.getenv("MCP_NOTIFIER", "noop").strip() or "noop"
    if backend == "noop":
        return NoopNotifier()
    if backend == "slack":
        # 지연 import — noop만 쓰는 배포는 Slack 백엔드(mcp SDK)를 로드하지 않는다
        # (llm/factory.py의 runpod 분기와 같은 처리).
        from .backends.slack import SlackNotifier

        return SlackNotifier()
    raise NotImplementedError(f"지원하지 않는 MCP_NOTIFIER: '{backend}'")
