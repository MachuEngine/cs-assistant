#!/usr/bin/env python3
"""실제 티켓 배치로 톤 평균 + 과정 지표를 측정한다 — NEXT_STEPS.md 우선순위 4,
DESIGN.md 6.2절 🟡(톤 평균)·🟢(과정 지표).

tone_golden(κ 측정용으로 선별된 30건)은 대표성이 부족해 "많은 실제 초안의
평균"을 재기엔 안 맞는다(EVAL.md 2026-08-01 "미측정 지표" 참고). 이 러너는
data/synthetic/tickets.jsonl(Bitext 실측, 26872건 — evals/golden이 아니라
일반 합성 데이터라 보호 경로 아님)에서 표본을 뽑아 실제 reply 파이프라인을
그대로 돌리고, auto_draft로 끝난 건의 judge tone 점수 평균과, 전체 건의
agent_turns(app/modules/reply/state.py 계측)·tool_calls·latency를 함께 잰다.

triage는 이 러너의 관심사가 아니다(그건 run_triage.py 몫) — tickets.jsonl에
이미 있는 Bitext 라벨(intent/category)을 그대로 triage_info로 써서 reply
에이전트만 비용을 태운다(run_escalation.py의 _run_full_pipeline과 동일한
선택). confidence는 고정 상수로 채워 E1 프리체크를 우회한다 — 이 표본으로
재는 건 triage 정확도가 아니라 reply 에이전트 산출물의 품질/과정이다.

run_triage.py에서 실측된 견고성 원칙과 동일하게, 한 건의 파이프라인 실패가
전체를 죽이지 않는다 — 실패는 outcome="__pipeline_error__"로 기록하고
집계에서 분모를 유지한다(tone/turns/latency 평균에서는 실패 건을 조용히
빼되, n_pipeline_failures로 항상 명시한다).
"""
import asyncio
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

os.environ.setdefault("LLM_BACKEND", "ollama")
os.environ.setdefault("JUDGE_BACKEND", "ollama")
os.environ.setdefault("OLLAMA_MODEL", "qwen2.5:14b")

from _common import load_jsonl, parse_args, write_report  # noqa: E402
from app.common.privacy import mask_pii  # noqa: E402
from app.modules.reply.graph import run_reply  # noqa: E402

TICKETS_PATH = "data/synthetic/tickets.jsonl"
DUMMY_CUSTOMER_ID = "CUST-000001"
_FIXED_CONFIDENCE = 0.95  # triage 정확도가 아니라 reply 산출물을 재는 러너라 E1 우회


async def _run_one(row: dict) -> dict:
    ticket = {
        "ticket_id": row["ticket_id"],
        "text": mask_pii(row["text"]),
        "customer_id": DUMMY_CUSTOMER_ID,
        "order_id": row.get("order_id", ""),
    }
    triage = {
        "intent": row["intent"], "category": row["category"],
        "confidence": _FIXED_CONFIDENCE, "requires_human": False,
    }

    start = time.monotonic()
    try:
        final_state = await run_reply(ticket, triage)
        error = None
    except Exception as e:
        final_state = None
        error = f"{type(e).__name__}: {e}"
    latency_s = round(time.monotonic() - start, 3)

    entry = {"ticket_id": row["ticket_id"], "intent": row["intent"],
              "latency_s": latency_s, "error": error}
    if final_state is None:
        entry.update({"outcome": "__pipeline_error__", "agent_turns": None,
                       "tool_calls": None, "tone": None})
        return entry

    outcome = final_state["outcome"]
    entry["outcome"] = outcome
    entry["agent_turns"] = final_state.get("agent_turns")
    entry["tool_calls"] = len(final_state["draft"]["tools_used"])
    entry["tone"] = final_state["judge_result"].get("tone") if outcome == "auto_draft" else None
    return entry


async def main() -> None:
    args = parse_args(default_sample=20)
    tickets = load_jsonl(TICKETS_PATH)
    sample = tickets if (args.full or args.all) else tickets[: args.sample]

    results = []
    for i, row in enumerate(sample, start=1):
        entry = await _run_one(row)
        results.append(entry)
        print(f"[{i}/{len(sample)}] {entry['ticket_id']} intent={entry['intent']} "
              f"outcome={entry['outcome']} turns={entry['agent_turns']} "
              f"tools={entry['tool_calls']} tone={entry['tone']} "
              f"latency={entry['latency_s']}s"
              + (f" ERROR={entry['error']}" if entry["error"] else ""))

    n = len(results)
    pipeline_failures = [r for r in results if r["error"]]
    auto_draft = [r for r in results if r["outcome"] == "auto_draft"]
    escalated = [r for r in results if r["outcome"] == "escalated"]
    tones = [r["tone"] for r in auto_draft if r["tone"] is not None]
    turns = [r["agent_turns"] for r in results if r["agent_turns"] is not None]
    tool_calls = [r["tool_calls"] for r in results if r["tool_calls"] is not None]
    latencies = [r["latency_s"] for r in results]

    def _avg(xs):
        return sum(xs) / len(xs) if xs else None

    report = {
        "n": n,
        "n_auto_draft": len(auto_draft),
        "n_escalated": len(escalated),
        "n_pipeline_failures": len(pipeline_failures),
        "tone_avg": _avg(tones),
        "tone_n": len(tones),
        "agent_turns_avg": _avg(turns),
        "tool_calls_avg": _avg(tool_calls),
        "latency_avg_s": _avg(latencies),
        "latency_p50_s": sorted(latencies)[len(latencies) // 2] if latencies else None,
        "latency_max_s": max(latencies) if latencies else None,
        "pipeline_failures": pipeline_failures,
        "results": results,
    }
    path = write_report("run_batch_metrics", report)

    print(f"\ntone_avg={report['tone_avg']} (n={report['tone_n']}, 목표 >= 4.0) "
          f"agent_turns_avg={report['agent_turns_avg']} tool_calls_avg={report['tool_calls_avg']}")
    print(f"latency_avg={report['latency_avg_s']}s p50={report['latency_p50_s']}s "
          f"max={report['latency_max_s']}s")
    print(f"outcome 분포: auto_draft={len(auto_draft)} escalated={len(escalated)} "
          f"pipeline_failure={len(pipeline_failures)} / n={n}")
    print(f"리포트 저장: {path}")


if __name__ == "__main__":
    asyncio.run(main())
