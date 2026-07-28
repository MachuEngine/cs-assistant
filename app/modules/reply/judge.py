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
) -> dict:
    """draft_text를 채점한다. llm은 get_judge_backend()가 반환한 LLMBackend."""
    content = json.dumps(
        {
            "ticket": ticket_text,
            "draft_reply": draft_text,
            "cited_policies": cited_policies,
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
