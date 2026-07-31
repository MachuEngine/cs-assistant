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
    # 값이 비어 있으면(`NOTICE_SOURCE=` 만 있는 .env) 미설정과 같이 본다 —
    # .env.example이 빈 값 스타일을 쓰기 때문에 흔히 밟는 함정인데, 여기서
    # NotImplementedError가 나면 check_live_notices가 그걸 '조회 실패'로 잡아
    # 공지 필수 인텐트가 전부 E9로 뒤집힌다. 미설정은 기능 끔이지 오류가 아니다.
    # 반면 오타(대소문자 포함)는 아래에서 그대로 실패시킨다 — 켰다고 믿는데
    # 꺼져 있는 상태가 가장 나쁘다.
    backend = os.getenv("NOTICE_SOURCE", "noop").strip() or "noop"
    if backend == "noop":
        return NoopNoticeSource()
    if backend == "stub":
        # 지연 import — noop만 쓰는 배포는 stub 모듈을 로드하지 않는다
        # (mcp/factory.py의 slack 분기와 같은 처리).
        from .backends.stub import StubNoticeSource

        return StubNoticeSource()
    if backend == "notion":
        from .backends.notion import NotionNoticeSource

        return NotionNoticeSource()
    raise NotImplementedError(f"지원하지 않는 NOTICE_SOURCE: '{backend}'")
