"""답변 초안 생성 — LangGraph ReAct Agent. DESIGN.md 2절 아키텍처.

노드: plan → agent → (judge | validate 직행 | end) → validate → (agent 재시도 | end)

분필의 exam 모듈 구조를 이식하되, 이 프로젝트는 스택 전체가 async라 분필의
"동기 그래프를 asyncio.to_thread로 감싸는" 복잡함이 필요 없다 — 그래프
자체를 async로 짜고 ainvoke()로 실행한다.
"""
import asyncio
import logging
import os
import pathlib
import re
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.common.llm import get_judge_backend, get_llm_backend

from .judge import judge_reply
from .state import ReplyState
from .tools import TOOLS, bind_session, get_ctx, init_session

logger = logging.getLogger(__name__)

_AGENT_PROMPT_PATH = pathlib.Path("prompts/reply_agent.md")

# tool_calls가 비었을 때, 모델이 정말 자발적으로 끝낸 것인지 도구 호출 형식이
# 깨진 것인지 구분하는 휴리스틱(분필과 동일, 2026-07-11 발견 패턴 재사용).
_BROKEN_TOOL_CALL_RE = re.compile(r'"name"\s*:\s*"|</?tool_call>', re.IGNORECASE)


def _looks_like_broken_tool_call(content) -> bool:
    return bool(_BROKEN_TOOL_CALL_RE.search(str(content or "")))


async def _invoke_with_retry(llm, messages, retries: int = 2, delay: float = 2.0):
    """간헐적인 로컬 LLM 스트림 오류를 흡수한다(분필 TROUBLESHOOTING.md 패턴)."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            return await llm.ainvoke(messages)
        except Exception as e:
            last_err = e
            logger.warning("LLM invoke 실패(%d/%d), 재시도: %s", attempt + 1, retries + 1, e)
            if attempt < retries:
                await asyncio.sleep(delay)
    raise last_err


def _load_agent_prompt() -> str:
    return _AGENT_PROMPT_PATH.read_text(encoding="utf-8")


def _build_system_prompt(state: ReplyState) -> str:
    ticket = state["ticket"]
    triage = state["triage"]

    sections = [
        _load_agent_prompt(),
        "\n## This ticket\n\n"
        f"Intent: {triage['intent']}\nCategory: {triage['category']}\n\n"
        f"Customer message:\n{ticket['text']}",
    ]
    if ticket.get("order_id"):
        sections.append(f"\nThe ticket references order ID: {ticket['order_id']}")
    if ticket.get("customer_id"):
        sections.append(f"Customer ID: {ticket['customer_id']}")

    feedback = state.get("validation_feedback", "")
    if feedback:
        sections.append(
            f"\n## Previous attempt was rejected\n\n{feedback}\n\n"
            "Do not just resubmit the same reply — actually fix the issues "
            "above, then save_draft and submit_for_review again."
        )

    return "\n".join(sections)


async def plan_node(state: ReplyState) -> dict:
    """세션 컨텍스트를 초기화한다. 요청 전체에서 단 한 번만 호출됨(재시도해도
    tools.py의 세션은 agent_node 안에서 계속 누적/갱신됨 — 여기서는 최초 1회
    리셋만 한다)."""
    init_session(
        ticket_text=state["ticket"]["text"],
        order_id=state["ticket"].get("order_id", ""),
        intent=state["triage"]["intent"],
    )
    return {}


async def agent_node(state: ReplyState) -> dict:
    """ReAct 에이전트가 티켓을 분석해 초안을 작성하거나 에스컬레이션을 요청한다."""
    ctx = get_ctx()
    system_prompt = _build_system_prompt(state)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="Follow the instructions above to draft a reply, or escalate."),
    ]

    llm = get_llm_backend().bind_tools(TOOLS)
    tool_map = {t.name: t for t in TOOLS}

    turn_cap = int(os.getenv("REPLY_TURN_CAP", "12"))
    malformed_cap = int(os.getenv("MALFORMED_TOOL_CALL_STREAK", "3"))
    malformed_streak = 0

    for _ in range(turn_cap):
        response = await _invoke_with_retry(llm, messages)
        messages.append(response)

        if not getattr(response, "tool_calls", []):
            incomplete = not (ctx["submitted"] or ctx["escalate_requested"])
            if incomplete and malformed_streak < malformed_cap:
                malformed_streak += 1
                content_str = str(response.content or "").strip()
                if _looks_like_broken_tool_call(response.content):
                    reason = (
                        "Your tool call format was broken. Try again — respond "
                        "with a proper tool call only, no explanation text."
                    )
                elif content_str:
                    # 모델이 잘 만든 답변을 도구 호출 없이 그냥 content로 써버리는
                    # 경우(2026-07-28 발견) — 일반적인 재시도 지시는 모델이 텍스트를
                    # 다시 새로 써서 게이트 실패를 유발할 수 있어, 방금 쓴 텍스트를
                    # 그대로 save_draft 인자로 넘기라고 명시한다.
                    reason = (
                        "You wrote your reply as plain text instead of calling a "
                        "tool. Do not rewrite or repeat it as plain text — call "
                        "save_draft right now with that exact text as the "
                        "reply_text argument."
                    )
                else:
                    reason = (
                        "You haven't saved and submitted a draft, or escalated, "
                        "yet. Try again — respond with a proper tool call only, "
                        "no explanation text."
                    )
                messages.append(HumanMessage(content=reason))
                continue
            break

        malformed_streak = 0
        for tc in response.tool_calls:
            fn = tool_map.get(tc["name"])
            if not fn:
                result_content = f"Unknown tool: {tc['name']}"
            else:
                try:
                    result_content = str(fn.invoke(tc["args"]))
                except Exception as e:
                    result_content = f"Tool call error — check argument types and retry: {e}"
            messages.append(ToolMessage(content=result_content, tool_call_id=tc["id"]))

        if ctx["submitted"] or ctx["escalate_requested"]:
            break

    updates: dict = {
        "draft": {
            "reply_text": ctx["draft_text"],
            "cited_policies": ctx["cited_policies"],
            "tools_used": ctx["tools_used"],
        },
        # REPLY_BUDGET("재시도 횟수")과 별개로 budget은 "남은 시도 횟수"를 뜻해,
        # run_reply()가 초기값을 REPLY_BUDGET+1로 세팅한다 — 여기서는 매 호출마다
        # 1씩만 깎으면 된다(분필과 동일 패턴, 오프셋만 다름).
        "budget": state["budget"] - 1,
    }

    save_draft_cap = int(os.getenv("SAVE_DRAFT_FAIL_STREAK", "3"))
    # order_not_found(E6)는 lookup_order가 실제로 확인한 결정론적 사실이라,
    # 에이전트가 그 사실을 이유로 스스로 escalate_to_human까지 호출해 둘 다
    # True인 경우에도 더 구체적인 근본 원인인 E6을 우선한다(2026-07-28 확인:
    # 두 플래그가 함께 서는 사례가 실제로 존재함).
    if ctx["order_not_found"]:
        updates["escalation_reason"] = "E6"
        updates["outcome"] = "escalated"
    elif ctx["escalate_requested"]:
        updates["escalation_reason"] = "E5"
        updates["outcome"] = "escalated"
    elif ctx["save_draft_fail_streak"] >= save_draft_cap:
        updates["escalation_reason"] = "E7"
        updates["outcome"] = "escalated"

    return updates


def route_after_agent(state: ReplyState) -> Literal["judge", "validate", "end"]:
    """E5/E6/E7이 확정되면 judge를 부르지 않고 바로 끝낸다(불필요한 Judge
    API 비용을 안 씀). 초안이 아직 없으면(턴 소진, 제출도 에스컬레이션도
    안 함) validate로 보내 동일한 예산 재시도 로직을 타게 한다."""
    if state.get("escalation_reason"):
        return "end"
    if not state["draft"]["reply_text"]:
        return "validate"
    return "judge"


async def judge_node(state: ReplyState) -> dict:
    """생성 모델과 분리된 벤더(get_judge_backend())로 초안을 채점한다.

    이 함수가 호출하는 judge_reply()는 오프라인 eval(Phase 7)도 동일하게
    호출한다 — 검증-배포 불일치를 처음부터 만들지 않는다(DESIGN.md 3.4절).
    """
    llm = get_judge_backend()
    result = await judge_reply(
        state["ticket"]["text"],
        state["draft"]["reply_text"],
        state["draft"]["cited_policies"],
        llm,
    )
    return {"judge_result": result}


def _build_judge_feedback(judge: dict, policy_threshold: int, tone_threshold: int, high_severity: list) -> str:
    parts = []
    if judge.get("policy_compliance", 0) < policy_threshold:
        parts.append(f"policy_compliance {judge.get('policy_compliance')} < {policy_threshold}")
    if judge.get("tone", 0) < tone_threshold:
        parts.append(f"tone {judge.get('tone')} < {tone_threshold}")
    if high_severity:
        joined = "; ".join(f"{v.get('type')}: {v.get('span', '')}" for v in high_severity)
        parts.append(f"high-severity violations: {joined}")
    reasoning = judge.get("reasoning", "")
    return f"{reasoning} | {'; '.join(parts)}" if reasoning else "; ".join(parts)


def validate_node(state: ReplyState) -> dict:
    """judge_result(있으면)를 threshold로 판정한다. gate ①~④는 이미
    save_draft 안에서 강제됐으므로 여기서 다시 검사하지 않는다 — 존재할 수
    없는 상태를 방어적으로 재검증하지 않는다."""
    draft_text = state["draft"]["reply_text"]

    if draft_text:
        judge = state["judge_result"]
        policy_threshold = int(os.getenv("JUDGE_PASS_POLICY", "4"))
        tone_threshold = int(os.getenv("JUDGE_PASS_TONE", "4"))
        high_severity = [v for v in judge.get("violations", []) if v.get("severity") == "high"]
        passed = (
            judge.get("policy_compliance", 0) >= policy_threshold
            and judge.get("tone", 0) >= tone_threshold
            and not high_severity
        )
        if passed:
            return {"validation_passed": True, "outcome": "auto_draft"}
        feedback = _build_judge_feedback(judge, policy_threshold, tone_threshold, high_severity)
    else:
        feedback = (
            "No draft was saved and submitted in the previous attempt. "
            "Call save_draft (until it succeeds) and then submit_for_review."
        )

    if state["budget"] > 0:
        return {"validation_passed": False, "validation_feedback": feedback}
    return {
        "validation_passed": False,
        "validation_feedback": feedback,
        "escalation_reason": "E8",
        "outcome": "escalated",
    }


def should_retry(state: ReplyState) -> Literal["agent", "end"]:
    if state.get("validation_passed"):
        return "end"
    if state.get("budget", 0) > 0:
        return "agent"
    return "end"


def build_reply_graph():
    g = StateGraph(ReplyState)
    g.add_node("plan", plan_node)
    g.add_node("agent", agent_node)
    g.add_node("judge", judge_node)
    g.add_node("validate", validate_node)

    g.add_edge(START, "plan")
    g.add_edge("plan", "agent")
    g.add_conditional_edges("agent", route_after_agent, {"judge": "judge", "validate": "validate", "end": END})
    g.add_edge("judge", "validate")
    g.add_conditional_edges("validate", should_retry, {"agent": "agent", "end": END})

    return g.compile()


_reply_graph = None


def get_reply_graph():
    global _reply_graph
    if _reply_graph is None:
        _reply_graph = build_reply_graph()
    return _reply_graph


async def run_reply(ticket: dict, triage: dict) -> dict:
    """공개 진입점.

    ticket: {ticket_id, text(마스킹된 본문), customer_id, order_id(없으면 "")}
    triage: {intent, category, confidence, requires_human}
    반환: ReplyState 전체(outcome/draft/judge_result/escalation_reason 포함)
    """
    bind_session()  # 그래프 실행 전, 그래프 밖에서 — tools.py의 bind_session() 참고
    graph = get_reply_graph()

    initial_state: ReplyState = {
        "ticket": ticket,
        "triage": triage,
        "draft": {"reply_text": "", "cited_policies": [], "tools_used": []},
        "judge_result": {},
        "validation_passed": False,
        "validation_feedback": "",
        # +1: REPLY_BUDGET은 "재시도 횟수"이므로 최초 시도 1회를 더해야
        # 총 시도 횟수가 된다(agent_node가 매 호출마다 1씩 깎는 방식이라
        # 여기서 오프셋을 보정해야 함 — agent_node 주석 참고).
        "budget": int(os.getenv("REPLY_BUDGET", "2")) + 1,
        "outcome": "",
        "escalation_reason": "",
    }
    return await graph.ainvoke(initial_state)
