"""전역 테스트 픽스처.

로컬 개발자의 실제 .env(MCP_NOTIFIER=slack 등 실제 배포값)가 테스트 결과를
좌우하면 안 된다 — CI에는 .env 자체가 없어 noop이 기본이지만, 이 값이 로컬에
남아있으면 관련 없는 테스트까지 실제 네트워크 연결을 시도하게 된다(Phase 11
도입 후 실제로 관측됨). MCP 동작 자체를 테스트하는 케이스는 각자
monkeypatch.setenv로 명시적으로 설정한다.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_mcp_env(monkeypatch):
    monkeypatch.delenv("MCP_NOTIFIER", raising=False)
    monkeypatch.delenv("SLACK_MCP_URL", raising=False)
    monkeypatch.delenv("SLACK_MCP_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_ESCALATION_CHANNEL", raising=False)
    monkeypatch.delenv("SLACK_MCP_TOOL_NAME", raising=False)
    # Phase 12a/12c — 공지 조회도 같은 이유로 격리한다(로컬 .env에 실제 노션
    # 토큰·DB ID가 들어 있어, 격리하지 않으면 어댑터 단위 테스트가 실제
    # 네트워크를 때리거나 개발자 환경에 따라 결과가 갈린다).
    monkeypatch.delenv("NOTICE_SOURCE", raising=False)
    monkeypatch.delenv("NOTICE_DEFAULT_TTL_DAYS", raising=False)
    monkeypatch.delenv("NOTICE_MAX_COUNT", raising=False)
    monkeypatch.delenv("NOTICE_MAX_BODY_CHARS", raising=False)
    monkeypatch.delenv("NOTICE_MCP_TIMEOUT", raising=False)
    monkeypatch.delenv("NOTICE_DB_ID", raising=False)
    monkeypatch.delenv("NOTION_MCP_URL", raising=False)
    monkeypatch.delenv("NOTION_MCP_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_DB_TOOL_NAME", raising=False)
    monkeypatch.delenv("NOTION_QUERY_TOOL_NAME", raising=False)
