"""답변 초안을 채점하는 로직 — 런타임(graph.py의 judge_node)과 오프라인
eval(Phase 7)이 이 judge_reply()를 공유한다(DESIGN.md 3.4절).

분필의 judge_structure()와 동일한 설계 원칙: 판단은 LLM(별도 벤더), 통과
여부는 코드(validate_node)가 결정한다. 이 프로젝트는 스택 전체가 async라
분필의 _run_async 스레드풀 래퍼가 필요 없다 — LLMBackend.generate()를
그대로 await한다.

호출 실패는 그대로 예외로 전파한다(fail-fast) — 조용히 폴백하면 신뢰도가
검증되지 않은 채로 게이트를 통과시키는 문제가 재발한다.
"""
import json
import pathlib

from app.common.llm import LLMBackend

_RUBRIC_PATH = pathlib.Path("prompts/judge_reply.md")


def _load_rubric() -> str:
    return _RUBRIC_PATH.read_text(encoding="utf-8")


async def judge_reply(
    ticket_text: str,
    draft_text: str,
    cited_policies: list,
    llm: LLMBackend,
    tool_results_log: list | None = None,
) -> dict:
    """draft_text를 채점한다. llm은 get_judge_backend()가 반환한 LLMBackend.

    tool_results_log: 이번 세션에서 search_policy/lookup_order/
    check_customer_tier가 실제로 반환한 텍스트(app.modules.reply.tools의
    contextvars 세션에 누적됨, save_draft 게이트②가 참조하는 것과 동일한
    로그). **이게 없으면 Judge는 "cited_policies" 조항 ID 문자열만 보고
    실제 조항 본문은 한 번도 못 본 채 policy_compliance를 채점하게 된다**
    (2026-07-29 실측 발견 — gpt-5.6-luna로 처음 실행했을 때 거의 모든
    초안이 "인용된 조항의 본문·도구 결과가 제공되지 않아 검증 불가"로
    policy_compliance=1을 받아 E8로 이어졌다. 로컬 Ollama Judge에서는
    이 결함이 상대적으로 안 드러났을 뿐 처음부터 있던 버그다).
    """
    content = json.dumps(
        {
            "ticket": ticket_text,
            "draft_reply": draft_text,
            "cited_policies": cited_policies,
            "retrieved_context": tool_results_log or [],
        },
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": _load_rubric()},
        {"role": "user", "content": content},
    ]
    raw = await llm.generate(messages)

    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        parsed = json.loads(raw[start:end]) if start >= 0 and end > start else {}
    except Exception:
        parsed = {}

    return {
        "policy_compliance": int(parsed.get("policy_compliance", 0)),
        "tone": int(parsed.get("tone", 0)),
        "violations": parsed.get("violations", []),
        "reasoning": parsed.get("reasoning", ""),
    }
