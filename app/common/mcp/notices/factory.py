"""공지 소스 진입점 — NOTICE_SOURCE 환경변수로 전환.

`app/common/mcp/factory.py`(알림 백엔드)와 같은 패턴. 기본값은 noop(기능
비활성) — 공지 조회가 없어도 파이프라인은 성립해야 한다(E9는 "미설정"이 아니라
"설정됐는데 실패"에서만 발동한다, PROMPTS.md Phase 12).

파이프라인 코드는 이 팩토리를 경유하고 백엔드 클래스를 직접 import 하지 않는다.
"""
import os

from .backends.noop import NoopNoticeSource
from .base import NoticeSource


def get_notice_source() -> NoticeSource:
    backend = os.getenv("NOTICE_SOURCE", "noop")
    if backend == "noop":
        return NoopNoticeSource()
    if backend == "stub":
        # 지연 import — noop만 쓰는 배포는 stub 모듈을 로드하지 않는다
        # (mcp/factory.py의 slack 분기와 같은 처리).
        from .backends.stub import StubNoticeSource

        return StubNoticeSource()
    raise NotImplementedError(f"지원하지 않는 NOTICE_SOURCE: '{backend}'")
