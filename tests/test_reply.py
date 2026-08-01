"""Reply 에이전트 테스트 (Phase 6 완료 기준).

단위 테스트(모델 호출 없음)는 save_draft 게이트 4종과 validate_node/
route_after_agent의 순수 라우팅 로직을 직접 검증한다. 완료 기준의 핵심인
"초안 생성 + escalated 케이스"는 pytest.mark.llm_live로 로컬 Ollama를 통해
그래프 전체를 실제로 돌려 확인한다.
"""
import json
import pathlib
import sqlite3

import pytest

from app.common.mcp.notices.backends import stub as notice_stub
from app.common.privacy import mask_pii
from app.modules.reply import tools as reply_tools
from app.modules.reply import graph as reply_graph
from app.modules.reply.graph import (
    agent_node,
    route_after_agent,
    run_reply,
    should_retry,
    stream_reply,
    validate_node,
)

TICKETS_PATH = pathlib.Path("data/synthetic/tickets.jsonl")
SHOP_DB_PATH = "data/synthetic/shop.db"

# 게이트⑤(고지 문구) 통과용 — 다른 게이트를 테스트하는 fixture 텍스트에 덧붙인다.
_DISCLAIMER = (
    "This is a draft prepared by an AI assistant. A human agent is "
    "responsible for reviewing and approving it before it is sent."
)


def _fresh_session(intent: str, ticket_text: str = "", order_id: str = ""):
    reply_tools.bind_session()
    reply_tools.init_session(ticket_text=ticket_text, order_id=order_id, intent=intent)


# --- save_draft 게이트 ①: PII 재유출 ----------------------------------------

def test_save_draft_rejects_unmasked_email():
    _fresh_session(intent="track_order")
    result = reply_tools.save_draft.invoke(
        {"reply_text": "Please reach us at jane.doe@example.com for any follow-up questions today."}
    )
    assert result.startswith("Rejected")
    assert reply_tools.get_ctx()["draft_text"] == ""


def test_save_draft_allows_masked_pii_tokens():
    _fresh_session(intent="track_order")
    result = reply_tools.save_draft.invoke(
        {"reply_text": f"We will contact {{{{EMAIL}}}} shortly with an update on your order status. {_DISCLAIMER}"}
    )
    assert result == "Draft saved."


# --- 게이트 ②: 근거 없는 확약 -----------------------------------------------

def test_save_draft_rejects_unsupported_amount():
    _fresh_session(intent="track_order")
    result = reply_tools.save_draft.invoke(
        {"reply_text": "We will refund you $49.99 immediately, no questions asked."}
    )
    assert result.startswith("Rejected")


def test_save_draft_allows_amount_actually_returned_by_tool():
    _fresh_session(intent="track_order")
    ctx = reply_tools.get_ctx()
    ctx["tool_results_log"].append("Order ORD-000001: status=delivered, amount=$49.99 USD")
    result = reply_tools.save_draft.invoke(
        {"reply_text": f"Your order total was $49.99 as shown in our system records today. {_DISCLAIMER}"}
    )
    assert result == "Draft saved."


# --- 게이트 ③: 금지 표현 ----------------------------------------------------

def test_save_draft_rejects_forbidden_phrase():
    _fresh_session(intent="track_order")
    result = reply_tools.save_draft.invoke(
        {"reply_text": "We guarantee this will be resolved immediately for you today."}
    )
    assert result.startswith("Rejected")


# --- 게이트 ④: 정책 인용 존재 -----------------------------------------------

def test_save_draft_rejects_missing_citation_when_required():
    _fresh_session(intent="cancel_order")
    result = reply_tools.save_draft.invoke(
        {"reply_text": "We have processed your cancellation request successfully today."}
    )
    assert result.startswith("Rejected")
    assert "citation" in result.lower()


def test_save_draft_accepts_valid_citation():
    _fresh_session(intent="cancel_order")
    result = reply_tools.save_draft.invoke({
        "reply_text": (
            "Per our cancellation policy [CANC-01], no fee applies since your "
            f"order has not yet started processing. {_DISCLAIMER}"
        )
    })
    assert result == "Draft saved."
    assert "CANC-01" in reply_tools.get_ctx()["cited_policies"]


def test_save_draft_no_citation_needed_for_account_intent():
    _fresh_session(intent="create_account")
    result = reply_tools.save_draft.invoke(
        {"reply_text": f"We have created your new account successfully, welcome aboard today. {_DISCLAIMER}"}
    )
    assert result == "Draft saved."


def test_save_draft_fail_streak_increments_and_resets():
    _fresh_session(intent="cancel_order")
    reply_tools.save_draft.invoke({"reply_text": "no citation here at all in this reply text"})
    reply_tools.save_draft.invoke({"reply_text": "still no citation present in this reply text"})
    assert reply_tools.get_ctx()["save_draft_fail_streak"] == 2

    reply_tools.save_draft.invoke(
        {"reply_text": f"Per policy [CANC-01] this request is resolved. {_DISCLAIMER}"}
    )
    assert reply_tools.get_ctx()["save_draft_fail_streak"] == 0


# --- 게이트 ⑤: 상담원 최종 책임 고지 ------------------------------------------

def test_save_draft_rejects_missing_disclaimer():
    _fresh_session(intent="create_account")
    result = reply_tools.save_draft.invoke(
        {"reply_text": "We have created your new account successfully, welcome aboard today."}
    )
    assert result.startswith("Rejected")
    assert "disclaimer" in result.lower()


def test_save_draft_accepts_disclaimer_with_minor_whitespace_variance():
    _fresh_session(intent="create_account")
    result = reply_tools.save_draft.invoke({
        "reply_text": (
            "We have created your new account successfully, welcome aboard "
            "today.\n\nThis is a draft prepared by an AI assistant.\n"
            "A human agent is responsible for reviewing and approving it "
            "before it is sent."
        )
    })
    assert result == "Draft saved."


# --- 나머지 도구 -------------------------------------------------------------

def test_submit_for_review_rejects_when_no_draft():
    _fresh_session(intent="track_order")
    result = reply_tools.submit_for_review.invoke({})
    assert "Rejected" in result
    assert reply_tools.get_ctx()["submitted"] is False


def test_submit_for_review_succeeds_after_save():
    _fresh_session(intent="create_account")
    reply_tools.save_draft.invoke(
        {"reply_text": f"Your account has been created successfully today. {_DISCLAIMER}"}
    )
    result = reply_tools.submit_for_review.invoke({})
    assert result == "Submitted for review."
    assert reply_tools.get_ctx()["submitted"] is True


def test_escalate_to_human_sets_flag():
    _fresh_session(intent="cancel_order")
    result = reply_tools.escalate_to_human.invoke({"reason": "order not found"})
    assert result == "Escalation recorded."
    assert reply_tools.get_ctx()["escalate_requested"] is True


def test_lookup_order_sets_not_found_flag_for_fake_order():
    _fresh_session(intent="track_order")
    result = reply_tools.lookup_order.invoke({"order_id": "ORD-999999"})
    assert "No order found" in result
    assert reply_tools.get_ctx()["order_not_found"] is True


def test_lookup_order_finds_real_order():
    conn = sqlite3.connect(SHOP_DB_PATH)
    row = conn.execute("SELECT order_id FROM orders LIMIT 1").fetchone()
    conn.close()

    _fresh_session(intent="track_order")
    result = reply_tools.lookup_order.invoke({"order_id": row[0]})
    assert "No order found" not in result
    assert row[0] in result


def test_validate_draft_format_rejects_too_short():
    result = reply_tools.validate_draft_format.invoke({"reply_text": "Sure."})
    assert "Format check failed" in result


def test_validate_draft_format_passes_normal_text():
    result = reply_tools.validate_draft_format.invoke({
        "reply_text": (
            "Thank you for reaching out. We have reviewed your request and "
            "confirmed the details below for you today."
        )
    })
    assert result == "Format check passed."


def test_validate_draft_format_allows_known_mask_tokens():
    result = reply_tools.validate_draft_format.invoke({
        "reply_text": (
            "We will contact {{EMAIL}} shortly with a full update regarding "
            "your recent order request today."
        )
    })
    assert result == "Format check passed."


# --- 게이트 ⑥: 라이브 공지 반영 누락 (Phase 12a) ------------------------------

def _shipping_notice(**overrides) -> dict:
    import datetime

    today = datetime.datetime.now(datetime.timezone.utc).date()
    base = {
        "notice_id": "N1",
        "title": "Shipping delay",
        "body": "Shipping is delayed by 3 days this week due to weather.",
        "scope": ["ORDER"],
        "valid_from": (today - datetime.timedelta(days=1)).isoformat(),
        "valid_until": (today + datetime.timedelta(days=365)).isoformat(),
        "active": True,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_notice_stub():
    notice_stub.reset()
    yield
    notice_stub.reset()


def test_save_draft_gate6_rejects_when_required_intent_never_called_check_live_notices(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    # track_order: NOTICE_REQUIRED이지만 SEARCH_POLICY_REQUIRED는 아니라서
    # 게이트④(인용 누락)에 가려지지 않고 게이트⑥만 단독으로 검증된다.
    _fresh_session(intent="track_order")  # NOTICE_REQUIRED, but tool never called
    result = reply_tools.save_draft.invoke(
        {"reply_text": f"Your package is on its way as expected. {_DISCLAIMER}"}
    )
    assert result.startswith("Rejected")
    assert "check_live_notices" in result


@pytest.mark.asyncio
async def test_save_draft_gate6_rejects_when_grounded_notice_not_applied(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    notice_stub.set_notices([_shipping_notice()])
    _fresh_session(intent="track_order")
    await reply_tools.check_live_notices.ainvoke({})

    result = reply_tools.save_draft.invoke(
        {"reply_text": f"Your package will arrive on the originally scheduled date. {_DISCLAIMER}"}
    )
    assert result.startswith("Rejected")
    assert "N1" in result


@pytest.mark.asyncio
async def test_save_draft_gate6_accepts_when_grounded_notice_applied(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    notice_stub.set_notices([_shipping_notice()])
    _fresh_session(intent="track_order")
    await reply_tools.check_live_notices.ainvoke({})

    result = reply_tools.save_draft.invoke({
        "reply_text": (
            f"Please note shipping is delayed by 3 days this week. {_DISCLAIMER}"
        ),
        "applied_notices": ["N1"],
    })
    assert result == "Draft saved."
    assert reply_tools.get_ctx()["applied_notices"] == ["N1"]


@pytest.mark.asyncio
async def test_save_draft_gate6_tolerates_csv_string_applied_notices(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    notice_stub.set_notices([_shipping_notice()])
    _fresh_session(intent="track_order")
    await reply_tools.check_live_notices.ainvoke({})

    result = reply_tools.save_draft.invoke({
        "reply_text": f"Please note shipping is delayed by 3 days this week. {_DISCLAIMER}",
        "applied_notices": "N1, N2",
    })
    assert result == "Draft saved."
    assert reply_tools.get_ctx()["applied_notices"] == ["N1", "N2"]


def test_gate_missing_notice_application_rejects_unparseable_value():
    # 도구 레이어(LangChain @tool의 pydantic 스키마)는 list[str]|str|None 외
    # 타입을 이미 걸러내므로(agent_node의 try/except가 그 예외를 자기교정
    # 메시지로 바꿔준다), 순수 파싱 실패 경로는 내부 함수를 직접 호출해 확인한다.
    _fresh_session(intent="track_order")  # NOTICE_REQUIRED 아님 — 조건①은 무관
    ctx = reply_tools.get_ctx()
    result = reply_tools._gate_missing_notice_application(ctx, {"not": "a list or string"})
    assert result is not None
    assert "applied_notices" in result


@pytest.mark.asyncio
async def test_save_draft_gate2_rejects_amount_from_scope_mismatched_notice(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    notice_stub.set_notices([
        _shipping_notice(notice_id="N2", scope=["PAYMENT"], body="A $75 credit applies to affected accounts."),
    ])
    _fresh_session(intent="delivery_period")  # category DELIVERY — scope 불일치
    await reply_tools.check_live_notices.ainvoke({})
    assert reply_tools.get_ctx()["grounded_notices"] == []  # scope 불일치라 근거 승격 안 됨

    result = reply_tools.save_draft.invoke({
        "reply_text": f"You will receive a $75 credit for the delay. {_DISCLAIMER}",
        "applied_notices": ["N2"],
    })
    assert result.startswith("Rejected")
    assert "$75" in result


def test_gate6_noop_is_a_noop(monkeypatch):
    monkeypatch.delenv("NOTICE_SOURCE", raising=False)  # 기본값 noop
    _fresh_session(intent="track_order")  # 필수 인텐트, check_live_notices 미호출
    result = reply_tools.save_draft.invoke(
        {"reply_text": f"Your package is on its way as expected. {_DISCLAIMER}"}
    )
    assert result == "Draft saved."  # noop이면 게이트⑥ 조건①이 적용되지 않는다


@pytest.mark.asyncio
async def test_save_draft_gate6_rejection_message_has_no_notice_body(monkeypatch):
    monkeypatch.setenv("NOTICE_SOURCE", "stub")
    notice_stub.set_notices([_shipping_notice(body="SECRET-BODY-TEXT-DO-NOT-LEAK")])
    _fresh_session(intent="track_order")
    await reply_tools.check_live_notices.ainvoke({})

    result = reply_tools.save_draft.invoke(
        {"reply_text": f"Your package will arrive as originally scheduled. {_DISCLAIMER}"}
    )
    assert result.startswith("Rejected")
    assert "N1" in result
    assert "SECRET-BODY-TEXT-DO-NOT-LEAK" not in result


# --- E9 에스컬레이션 우선순위 (E6 > E9 > E5 > E7) ------------------------------

class _FakeNoToolCallResponse:
    content = ""
    tool_calls = []


class _FakeBoundLLM:
    async def ainvoke(self, messages):
        return _FakeNoToolCallResponse()


class _FakeLLMBackend:
    def bind_tools(self, tools):
        return _FakeBoundLLM()


def _agent_state(intent: str, category: str = "DELIVERY") -> dict:
    return {
        "ticket": {"text": "hello", "order_id": "", "customer_id": ""},
        "triage": {"intent": intent, "category": category, "confidence": 0.9, "requires_human": False},
        "budget": 2,
        "validation_feedback": "",
    }


@pytest.mark.asyncio
async def test_agent_node_e6_priority_over_e9(monkeypatch):
    monkeypatch.setattr(reply_graph, "get_llm_backend", lambda: _FakeLLMBackend())
    _fresh_session(intent="delivery_period")
    ctx = reply_tools.get_ctx()
    ctx["order_not_found"] = True
    ctx["notice_lookup_failed"] = True
    ctx["escalate_requested"] = True  # 루프를 즉시 끝내기 위한 종료 조건

    result = await agent_node(_agent_state("delivery_period"))
    assert result["escalation_reason"] == "E6"


@pytest.mark.asyncio
async def test_agent_node_e9_priority_over_e5(monkeypatch):
    monkeypatch.setattr(reply_graph, "get_llm_backend", lambda: _FakeLLMBackend())
    _fresh_session(intent="delivery_period")
    ctx = reply_tools.get_ctx()
    ctx["notice_lookup_failed"] = True
    ctx["escalate_requested"] = True

    result = await agent_node(_agent_state("delivery_period"))
    assert result["escalation_reason"] == "E9"


@pytest.mark.asyncio
async def test_agent_node_e9_not_triggered_for_non_required_intent(monkeypatch):
    monkeypatch.setattr(reply_graph, "get_llm_backend", lambda: _FakeLLMBackend())
    _fresh_session(intent="cancel_order")  # NOTICE_REQUIRED 아님
    ctx = reply_tools.get_ctx()
    ctx["notice_lookup_failed"] = True  # 방어적 조건 — 무관해야 함
    ctx["escalate_requested"] = True

    result = await agent_node(_agent_state("cancel_order", category="ORDER"))
    assert result["escalation_reason"] == "E5"


# --- agent_turns 계측 (과정 지표, NEXT_STEPS.md 우선순위 4) ------------------

@pytest.mark.asyncio
async def test_agent_node_counts_single_turn(monkeypatch):
    monkeypatch.setattr(reply_graph, "get_llm_backend", lambda: _FakeLLMBackend())
    _fresh_session(intent="cancel_order")
    ctx = reply_tools.get_ctx()
    ctx["escalate_requested"] = True  # 첫 턴에 바로 루프 종료

    result = await agent_node(_agent_state("cancel_order", category="ORDER"))
    assert result["agent_turns"] == 1


@pytest.mark.asyncio
async def test_agent_node_accumulates_turns_across_retries(monkeypatch):
    """budget 소진 없이 validate_node가 재시도를 보낼 때, agent_node가 이전
    호출까지의 누적값(state["agent_turns"])에 이번 호출분만 더해야 한다
    (budget 필드와 동일 패턴 — LangGraph는 기본이 덮어쓰기라 직접 누적)."""
    monkeypatch.setattr(reply_graph, "get_llm_backend", lambda: _FakeLLMBackend())
    _fresh_session(intent="cancel_order")
    ctx = reply_tools.get_ctx()
    ctx["escalate_requested"] = True

    state = _agent_state("cancel_order", category="ORDER")
    state["agent_turns"] = 3  # 이전 재시도에서 이미 3턴 썼다고 가정

    result = await agent_node(state)
    assert result["agent_turns"] == 4


# --- validate_node / 라우팅 (순수 함수, 모델 호출 없음) --------------------

def test_validate_node_passes_when_scores_meet_threshold(monkeypatch):
    monkeypatch.setenv("JUDGE_PASS_POLICY", "4")
    monkeypatch.setenv("JUDGE_PASS_TONE", "4")
    state = {
        "draft": {"reply_text": "some reply", "cited_policies": [], "tools_used": []},
        "judge_result": {"policy_compliance": 4, "tone": 5, "violations": [], "reasoning": "ok"},
        "budget": 2,
    }
    result = validate_node(state)
    assert result == {"validation_passed": True, "outcome": "auto_draft"}


def test_validate_node_fails_on_high_severity_violation(monkeypatch):
    monkeypatch.setenv("JUDGE_PASS_POLICY", "4")
    monkeypatch.setenv("JUDGE_PASS_TONE", "4")
    state = {
        "draft": {"reply_text": "some reply", "cited_policies": [], "tools_used": []},
        "judge_result": {
            "policy_compliance": 5, "tone": 5,
            "violations": [{"type": "pii_leak", "span": "x", "severity": "high"}],
            "reasoning": "leaked pii",
        },
        "budget": 2,
    }
    result = validate_node(state)
    assert result["validation_passed"] is False
    assert "outcome" not in result


def test_validate_node_escalates_e8_when_budget_exhausted(monkeypatch):
    monkeypatch.setenv("JUDGE_PASS_POLICY", "4")
    monkeypatch.setenv("JUDGE_PASS_TONE", "4")
    state = {
        "draft": {"reply_text": "some reply", "cited_policies": [], "tools_used": []},
        "judge_result": {"policy_compliance": 2, "tone": 2, "violations": [], "reasoning": "bad"},
        "budget": 0,
    }
    result = validate_node(state)
    assert result["escalation_reason"] == "E8"
    assert result["outcome"] == "escalated"


def test_validate_node_no_draft_retries_when_budget_left():
    state = {"draft": {"reply_text": "", "cited_policies": [], "tools_used": []}, "judge_result": {}, "budget": 1}
    result = validate_node(state)
    assert result["validation_passed"] is False
    assert "escalation_reason" not in result


def test_validate_node_no_draft_escalates_e8_when_budget_exhausted():
    state = {"draft": {"reply_text": "", "cited_policies": [], "tools_used": []}, "judge_result": {}, "budget": 0}
    result = validate_node(state)
    assert result["escalation_reason"] == "E8"
    assert result["outcome"] == "escalated"


def test_should_retry_routing():
    assert should_retry({"validation_passed": True, "budget": 0}) == "end"
    assert should_retry({"validation_passed": False, "budget": 1}) == "agent"
    assert should_retry({"validation_passed": False, "budget": 0}) == "end"


def test_route_after_agent_escalation_goes_to_end():
    assert route_after_agent({"escalation_reason": "E5", "draft": {"reply_text": ""}}) == "end"


def test_route_after_agent_no_draft_goes_to_validate():
    assert route_after_agent({"escalation_reason": "", "draft": {"reply_text": ""}}) == "validate"


def test_route_after_agent_with_draft_goes_to_judge():
    assert route_after_agent({"escalation_reason": "", "draft": {"reply_text": "hello"}}) == "judge"


class _FakeCompiledGraph:
    """astream(stream_mode="updates")을 흉내내는 스텁 — 실제 LangGraph는 노드가
    빈 dict({})를 반환해도 None을 내보낸다(2026-07-29, 실측으로 발견한 버그:
    stream_reply()가 이걸 그대로 state.update(None)에 넘겨 TypeError가 났었다).
    이 테스트는 LLM 없이 그 케이스를 재현해 회귀를 막는다."""

    def __init__(self, updates):
        self._updates = updates

    async def astream(self, state, stream_mode="updates"):
        for update in self._updates:
            yield update


@pytest.mark.asyncio
async def test_stream_reply_handles_none_partial_from_empty_node_update(monkeypatch):
    """plan_node처럼 빈 dict를 반환하는 노드는 LangGraph updates 모드에서
    {"plan": None}으로 나온다 — state.update()에 None을 그대로 넘기면 안 된다."""
    fake_graph = _FakeCompiledGraph([
        {"plan": None},
        {"agent": {
            "draft": {"reply_text": "", "cited_policies": [], "tools_used": []},
            "budget": 2,
        }},
        {"validate": {
            "validation_passed": False,
            "validation_feedback": "no draft",
            "escalation_reason": "E8",
            "outcome": "escalated",
        }},
    ])
    monkeypatch.setattr(reply_graph, "get_reply_graph", lambda: fake_graph)
    reply_tools.bind_session()

    events = []
    async for event in stream_reply(
        {"ticket_id": "T1", "text": "hi", "customer_id": "", "order_id": ""},
        {"intent": "cancel_order", "category": "ORDER", "confidence": 0.9, "requires_human": False},
    ):
        events.append(event)

    stages = [e["stage"] for e in events if e["status"] == "progress"]
    assert stages == ["plan", "agent", "validate"]
    assert events[-1] == {"status": "done", "outcome": "escalated", "escalation_reason": "E8"}


# --- 완료 기준: 그래프 전체를 실제 Ollama로 실행 ----------------------------

def _find_ticket(intent: str, order_exists: bool) -> dict:
    with open(TICKETS_PATH, encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            if t["intent"] == intent and t["order_exists"] == order_exists:
                return t
    raise AssertionError(f"no ticket found for intent={intent} order_exists={order_exists}")


def _to_reply_ticket(raw: dict) -> dict:
    return {
        "ticket_id": raw["ticket_id"],
        "text": mask_pii(raw["text"]),
        "customer_id": raw["customer_id"],
        "order_id": raw["order_id"],
    }


@pytest.mark.llm_live
@pytest.mark.asyncio
async def test_auto_draft_path_with_real_order(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    monkeypatch.setenv("JUDGE_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:14b")

    raw_ticket = _find_ticket("cancel_order", True)
    ticket = _to_reply_ticket(raw_ticket)
    triage = {"intent": "cancel_order", "category": "ORDER", "confidence": 0.95, "requires_human": False}

    final_state = await run_reply(ticket, triage)
    print(
        f"\n[auto_draft] outcome={final_state['outcome']} "
        f"escalation={final_state['escalation_reason']!r} "
        f"cited={final_state['draft']['cited_policies']} "
        f"judge={final_state['judge_result']}"
    )

    assert final_state["outcome"] == "auto_draft"
    assert final_state["draft"]["cited_policies"], "정책 인용이 비어있다"
    assert final_state["judge_result"]["policy_compliance"] >= 4
    assert final_state["judge_result"]["tone"] >= 4
    assert "AI assistant" in final_state["draft"]["reply_text"]  # 상담원 책임 고지 확인


@pytest.mark.llm_live
@pytest.mark.asyncio
async def test_escalated_path_with_nonexistent_order(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    monkeypatch.setenv("JUDGE_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:14b")

    raw_ticket = _find_ticket("cancel_order", False)
    ticket = _to_reply_ticket(raw_ticket)
    triage = {"intent": "cancel_order", "category": "ORDER", "confidence": 0.95, "requires_human": False}

    final_state = await run_reply(ticket, triage)
    print(
        f"\n[escalated] outcome={final_state['outcome']} "
        f"escalation={final_state['escalation_reason']!r}"
    )

    assert final_state["outcome"] == "escalated"
    assert final_state["escalation_reason"] == "E6"
