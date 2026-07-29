"""FastAPI 라우트 테스트 (Phase 9 백엔드).

단위 테스트(모델 호출 없음)는 인증·429·413·outcome 분기 형태를 triage_ticket/
run_reply/stream_reply를 monkeypatch해 검증한다. pytest.mark.llm_live 2건만
로컬 Ollama로 파이프라인 전체를 실제로 태운다(app/main.py가 그래프를 올바르게
호출하는지의 최종 확인 — test_reply.py가 이미 그래프 자체는 검증했으므로
여기서는 "API 계층이 그 결과를 올바른 모양으로 감싸는가"에 집중한다).
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app

client = TestClient(app)

API_KEY = "test-api-key"
HEADERS = {"X-API-Key": API_KEY}

_DISCLAIMER = (
    "This is a draft prepared by an AI assistant. A human agent is "
    "responsible for reviewing and approving it before it is sent."
)


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setenv("CS_API_KEY", API_KEY)


# --- /health: 인증 불필요 ----------------------------------------------------

def test_health_no_auth_required():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- 인증 -------------------------------------------------------------------

def test_triage_missing_api_key_returns_401():
    response = client.post("/triage", json={"ticket_text": "hello"})
    assert response.status_code == 401


def test_triage_wrong_api_key_returns_401():
    response = client.post(
        "/triage", json={"ticket_text": "hello"}, headers={"X-API-Key": "wrong"}
    )
    assert response.status_code == 401


def test_reply_missing_api_key_returns_401():
    response = client.post("/reply", json={"ticket_text": "hello"})
    assert response.status_code == 401


def test_reply_stream_missing_api_key_returns_401():
    response = client.post("/reply/stream", json={"ticket_text": "hello"})
    assert response.status_code == 401


# --- /triage 응답 형태 --------------------------------------------------------

async def _fake_triage_ok(ticket_text, flags=""):
    return {
        "intent": "cancel_order",
        "category": "ORDER",
        "confidence": 0.93,
        "requires_human": False,
        "reason": "customer wants to cancel an order",
        "escalation_reason": None,
    }


def test_triage_endpoint_returns_expected_shape(monkeypatch):
    monkeypatch.setattr(main, "triage_ticket", _fake_triage_ok)
    response = client.post("/triage", json={"ticket_text": "cancel my order"}, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "cancel_order"
    assert body["category"] == "ORDER"
    assert body["requires_human"] is False
    assert "escalation_reason" not in body  # /triage 계약에 없는 필드


# --- /reply: 사전 에스컬레이션(E1~E4)은 초안 없이 사유만 ------------------------

async def _fake_triage_escalated(ticket_text, flags=""):
    return {
        "intent": "contact_human_agent",
        "category": "CONTACT",
        "confidence": 0.97,
        "requires_human": True,
        "reason": "customer explicitly asked for a human agent",
        "escalation_reason": "E2",
    }


def test_reply_pre_agent_escalation_has_no_draft_field(monkeypatch):
    monkeypatch.setattr(main, "triage_ticket", _fake_triage_escalated)
    response = client.post(
        "/reply", json={"ticket_text": "let me talk to a human"}, headers=HEADERS
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "escalated"
    assert body["escalation_reason"] == "E2"
    assert "draft" not in body
    assert "cited_policies" not in body


# --- /reply: auto_draft 형태 --------------------------------------------------

async def _fake_run_reply_auto_draft(ticket, triage_info):
    return {
        "outcome": "auto_draft",
        "escalation_reason": "",
        "draft": {
            "reply_text": f"Sure, I can help with that.\n\n[RET-02]\n\n{_DISCLAIMER}",
            "cited_policies": ["RET-02"],
            "tools_used": ["search_policy", "lookup_order"],
        },
        "judge_result": {
            "policy_compliance": 5,
            "tone": 5,
            "violations": [],
            "reasoning": "well supported and on-tone",
        },
    }


def test_reply_auto_draft_shape(monkeypatch):
    monkeypatch.setattr(main, "triage_ticket", _fake_triage_ok)
    monkeypatch.setattr(main, "run_reply", _fake_run_reply_auto_draft)
    response = client.post(
        "/reply",
        json={"ticket_text": "please cancel order ORD-000003", "customer_id": "CUST-000666"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "auto_draft"
    assert _DISCLAIMER in body["draft"]
    assert body["cited_policies"] == ["RET-02"]
    assert "search_policy" in body["tools_used"]
    assert body["judge_result"]["policy_compliance"] == 5
    assert body["triage"]["intent"] == "cancel_order"


# --- /reply: 파이프라인 예외는 내부 상세 없이 outcome=failed로 -----------------

async def _raise_triage(ticket_text, flags=""):
    raise RuntimeError("boom — some internal exception detail")


def test_reply_failed_outcome_hides_internal_detail(monkeypatch):
    monkeypatch.setattr(main, "triage_ticket", _raise_triage)
    response = client.post("/reply", json={"ticket_text": "hello"}, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body == {"outcome": "failed"}
    assert "boom" not in json.dumps(body)


# --- 동시 요청 제한(429) ------------------------------------------------------

def test_reply_returns_429_when_concurrency_slot_unavailable(monkeypatch):
    monkeypatch.setattr(main, "_reply_semaphore", asyncio.Semaphore(0))
    response = client.post("/reply", json={"ticket_text": "hello"}, headers=HEADERS)
    assert response.status_code == 429


def test_reply_stream_returns_429_when_concurrency_slot_unavailable(monkeypatch):
    monkeypatch.setattr(main, "_reply_semaphore", asyncio.Semaphore(0))
    response = client.post("/reply/stream", json={"ticket_text": "hello"}, headers=HEADERS)
    assert response.status_code == 429


# --- 요청 크기 제한(413) ------------------------------------------------------

def test_oversized_request_returns_413():
    # 미들웨어는 app 생성 시점에 MAX_REQUEST_BYTES(기본 32KB)를 고정 캡처하므로
    # (요청마다 env 재조회하지 않음), monkeypatch로 임계값을 낮추는 대신 실제로
    # 그보다 큰 본문을 보낸다.
    big_text = "x" * (main.MAX_REQUEST_BYTES + 1000)
    response = client.post("/triage", json={"ticket_text": big_text}, headers=HEADERS)
    assert response.status_code == 413


# --- /reply/stream SSE 프레이밍 -----------------------------------------------

def _parse_sse_events(raw_text: str) -> list[dict]:
    events = []
    for chunk in raw_text.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        assert chunk.startswith("data: ")
        events.append(json.loads(chunk[len("data: "):]))
    return events


async def _fake_stream_reply_auto_draft(ticket, triage_info):
    yield {"status": "progress", "stage": "plan"}
    yield {"status": "progress", "stage": "agent"}
    yield {"status": "progress", "stage": "judge"}
    yield {"status": "progress", "stage": "validate"}
    yield {
        "status": "done",
        "outcome": "auto_draft",
        "draft": f"Sure.\n\n[RET-02]\n\n{_DISCLAIMER}",
        "cited_policies": ["RET-02"],
        "tools_used": ["search_policy"],
        "judge_result": {"policy_compliance": 5, "tone": 5, "violations": [], "reasoning": "ok"},
    }


def test_reply_stream_emits_triage_first_then_progress_then_done(monkeypatch):
    monkeypatch.setattr(main, "triage_ticket", _fake_triage_ok)
    monkeypatch.setattr(main, "stream_reply", _fake_stream_reply_auto_draft)

    response = client.post(
        "/reply/stream", json={"ticket_text": "cancel order ORD-000003"}, headers=HEADERS
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse_events(response.text)
    assert events[0] == {
        "status": "progress",
        "stage": "triage",
        "intent": "cancel_order",
        "category": "ORDER",
        "confidence": 0.93,
    }
    stages = [e["stage"] for e in events[1:-1]]
    assert stages == ["plan", "agent", "judge", "validate"]
    final = events[-1]
    assert final["status"] == "done"
    assert final["outcome"] == "auto_draft"
    assert _DISCLAIMER in final["draft"]


def test_reply_stream_escalated_has_no_draft_anywhere(monkeypatch):
    monkeypatch.setattr(main, "triage_ticket", _fake_triage_escalated)

    response = client.post(
        "/reply/stream", json={"ticket_text": "let me talk to a human"}, headers=HEADERS
    )
    events = _parse_sse_events(response.text)
    # triage progress + notify progress(Phase 11) + done(escalated), agent 단계 없음
    assert len(events) == 3
    assert events[1] == {"status": "progress", "stage": "notify"}
    final = events[-1]
    assert final == {"status": "done", "outcome": "escalated", "escalation_reason": "E2"}
    # 초안을 흐릿하게라도 흘리지 않는다 — draft 관련 키가 전혀 없어야 함
    assert not any("draft" in e for e in events)


# --- Phase 11: 에스컬레이션 알림 4개 지점 배선 -------------------------------

class _FakeNotifier:
    """get_notifier()를 대체해 notify_escalation 호출 여부·인자를 기록한다."""

    def __init__(self):
        self.calls = []

    async def notify_escalation(self, **kwargs):
        self.calls.append(kwargs)
        return True


def test_reply_notifies_on_pre_agent_escalation(monkeypatch):
    fake = _FakeNotifier()
    monkeypatch.setattr(main, "triage_ticket", _fake_triage_escalated)
    monkeypatch.setattr(main, "get_notifier", lambda: fake)

    response = client.post(
        "/reply",
        json={"ticket_text": "let me talk to a human", "ticket_ref": "ZENDESK-42"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["ticket_ref"] == "ZENDESK-42"
    assert call["escalation_reason"] == "E2"
    assert call["intent"] == "contact_human_agent"
    # 페이로드에 본문·customer_id가 안 실린다 — 넘기는 인자 자체에 없다
    assert "ticket_text" not in call and "customer_id" not in call


async def _fake_run_reply_escalated_post_agent(ticket, triage_info):
    return {
        "outcome": "escalated",
        "escalation_reason": "E6",
        "draft": {"reply_text": "", "cited_policies": [], "tools_used": []},
        "judge_result": {},
    }


def test_reply_notifies_on_post_agent_escalation(monkeypatch):
    fake = _FakeNotifier()
    monkeypatch.setattr(main, "triage_ticket", _fake_triage_ok)
    monkeypatch.setattr(main, "run_reply", _fake_run_reply_escalated_post_agent)
    monkeypatch.setattr(main, "get_notifier", lambda: fake)

    response = client.post(
        "/reply", json={"ticket_text": "cancel order ORD-999999"}, headers=HEADERS
    )
    assert response.status_code == 200
    assert len(fake.calls) == 1
    assert fake.calls[0]["escalation_reason"] == "E6"


def test_reply_does_not_notify_on_auto_draft(monkeypatch):
    fake = _FakeNotifier()
    monkeypatch.setattr(main, "triage_ticket", _fake_triage_ok)
    monkeypatch.setattr(main, "run_reply", _fake_run_reply_auto_draft)
    monkeypatch.setattr(main, "get_notifier", lambda: fake)

    response = client.post("/reply", json={"ticket_text": "cancel my order"}, headers=HEADERS)
    assert response.status_code == 200
    assert fake.calls == []


def test_reply_stream_notifies_on_pre_agent_escalation(monkeypatch):
    fake = _FakeNotifier()
    monkeypatch.setattr(main, "triage_ticket", _fake_triage_escalated)
    monkeypatch.setattr(main, "get_notifier", lambda: fake)

    response = client.post(
        "/reply/stream",
        json={"ticket_text": "let me talk to a human", "ticket_ref": "ZENDESK-7"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert len(fake.calls) == 1
    assert fake.calls[0]["ticket_ref"] == "ZENDESK-7"


async def _fake_stream_reply_escalated_post_agent(ticket, triage_info):
    yield {"status": "progress", "stage": "plan"}
    yield {"status": "progress", "stage": "agent"}
    yield {"status": "done", "outcome": "escalated", "escalation_reason": "E8"}


def test_reply_stream_notifies_on_post_agent_escalation(monkeypatch):
    fake = _FakeNotifier()
    monkeypatch.setattr(main, "triage_ticket", _fake_triage_ok)
    monkeypatch.setattr(main, "stream_reply", _fake_stream_reply_escalated_post_agent)
    monkeypatch.setattr(main, "get_notifier", lambda: fake)

    response = client.post(
        "/reply/stream", json={"ticket_text": "cancel order ORD-000003"}, headers=HEADERS
    )
    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert len(fake.calls) == 1
    assert fake.calls[0]["escalation_reason"] == "E8"
    # notify 진행 이벤트가 done 직전에 딱 한 번
    assert [e for e in events if e.get("stage") == "notify"] == [{"status": "progress", "stage": "notify"}]


def test_reply_stream_does_not_notify_on_auto_draft(monkeypatch):
    fake = _FakeNotifier()
    monkeypatch.setattr(main, "triage_ticket", _fake_triage_ok)
    monkeypatch.setattr(main, "stream_reply", _fake_stream_reply_auto_draft)
    monkeypatch.setattr(main, "get_notifier", lambda: fake)

    response = client.post(
        "/reply/stream", json={"ticket_text": "cancel order ORD-000003"}, headers=HEADERS
    )
    assert response.status_code == 200
    assert fake.calls == []


def test_notify_escalation_failure_does_not_flip_outcome_to_failed(monkeypatch):
    """fail-soft 핵심 계약 — 알림 채널 장애가 멀쩡한 에스컬레이션 판정을
    outcome=failed로 뒤집으면 안 된다(MCP_INTEGRATION.md 3.5절)."""

    class _BoomNotifier:
        async def notify_escalation(self, **kwargs):
            raise RuntimeError("slack is down")

    monkeypatch.setattr(main, "triage_ticket", _fake_triage_escalated)
    monkeypatch.setattr(main, "get_notifier", lambda: _BoomNotifier())

    response = client.post(
        "/reply", json={"ticket_text": "let me talk to a human"}, headers=HEADERS
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "escalated"
    assert body["escalation_reason"] == "E2"


# --- 주문번호 추출 helper -----------------------------------------------------

def test_extract_order_id_finds_pattern():
    assert main._extract_order_id("please cancel ORD-000123 asap") == "ORD-000123"


def test_extract_order_id_returns_empty_when_absent():
    assert main._extract_order_id("please cancel my most recent order") == ""


# --- 완료 기준: 실제 로컬 Ollama로 API 계층을 관통시켜 확인 --------------------

@pytest.mark.llm_live
def test_live_reply_stream_pre_agent_escalation(monkeypatch):
    """E2(사람 요청) 티켓은 agent 단계 없이 즉시 escalated로 끝나야 한다 —
    LLM은 triage 1회만 호출하므로 llm_live 중 가장 빠른 케이스."""
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    monkeypatch.setenv("JUDGE_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:14b")

    response = client.post(
        "/reply/stream",
        json={"ticket_text": "I don't want your bot, connect me to a real human agent right now"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert events[0]["status"] == "progress"
    assert events[0]["stage"] == "triage"
    final = events[-1]
    assert final["status"] == "done"
    # E2(contact_human_agent) 또는 E1(confidence 미달)로 갈 수 있으나 둘 다
    # escalated이며 draft가 전혀 없어야 한다는 계약은 동일하다.
    assert final["outcome"] == "escalated"
    assert not any("draft" in e for e in events)


@pytest.mark.llm_live
def test_live_reply_non_stream_auto_draft_or_escalation(monkeypatch):
    """실제 hydrate된 티켓(취소 가능한 미배송 주문)으로 /reply(비스트리밍)를
    실제로 관통시킨다. 로컬 소형 모델이라 auto_draft를 못 내고 budget 소진
    (E8)으로 escalate할 수도 있음 — 이 테스트는 "API 계층이 깨지지 않고 셋 중
    하나의 유효한 outcome을 반환하는가"만 확인한다(모델 품질 자체는
    test_reply.py의 llm_live 테스트가 이미 검증)."""
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    monkeypatch.setenv("JUDGE_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:14b")

    response = client.post(
        "/reply",
        json={
            "ticket_text": "Hi, can you cancel order ORD-000003 for me? It hasn't arrived yet.",
            "customer_id": "CUST-000666",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] in ("auto_draft", "escalated", "failed")
    if body["outcome"] == "auto_draft":
        assert _DISCLAIMER in body["draft"]
    elif body["outcome"] == "escalated":
        assert body["escalation_reason"] in (
            "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8",
        )
