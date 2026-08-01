#!/usr/bin/env python3
"""escalation_golden.jsonl 기반 에스컬레이션 평가 — DESIGN.md 6.2절 ⭐.

E1~E4는 app.modules.reply.routing.check_pre_agent_escalation()만으로
결정론적으로 판정 가능해 LLM 호출 없이 전부(--sample과 무관하게) 돌린다.
E6/none(대조군)/E5/E7/E8은 Phase 6 reply agent를 실제로 실행해야 확정되는
결과라 --sample 개수만큼만 돈다(비용 발생 — DESIGN.md 6.4절).

E5/E7/E8은 골든셋 생성 스크립트가 이미 "결정론적으로 보장 못 함"으로
표시해뒀다(best_effort) — 여기서는 정확한 사유코드 일치가 아니라
"에스컬레이션이 일어났는가"(recall)만 채점한다.

[2026-08-01 수정] run_triage.py에서 실측된 것과 같은 결함 — _run_full_pipeline()이
LLM/네트워크를 부르는데 실패를 못 잡아서 --full 도중 한 건만 실패해도 전체가
죽고 그때까지의 API 비용이 리포트 없이 날아갔다. 한 건의 파이프라인 실패를
"에스컬레이션 미달성"(recall 분모에는 남되 분자는 안 채움)으로 기록하고 계속
진행하도록 고친다.
"""
import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

os.environ.setdefault("LLM_BACKEND", "ollama")
os.environ.setdefault("JUDGE_BACKEND", "ollama")
os.environ.setdefault("OLLAMA_MODEL", "qwen2.5:14b")

from _common import load_jsonl, parse_args, write_report  # noqa: E402
from app.common.privacy import mask_pii  # noqa: E402
from app.modules.reply.graph import run_reply  # noqa: E402
from app.modules.reply.routing import check_pre_agent_escalation  # noqa: E402

GOLDEN_PATH = "evals/golden/escalation_golden.jsonl"
FAST_SCENARIOS = {"E1", "E2", "E3", "E4"}
DUMMY_CUSTOMER_ID = "CUST-000001"


async def _run_full_pipeline(row: dict) -> dict:
    triage_in = row["triage"]
    ticket = {
        "ticket_id": row["ticket_id"],
        "text": mask_pii(row["ticket_text"]),
        "customer_id": DUMMY_CUSTOMER_ID,
        "order_id": row.get("order_id", ""),
    }
    triage = {
        "intent": triage_in["intent"], "category": "", "confidence": triage_in["confidence"],
        "requires_human": False,
    }
    return await run_reply(ticket, triage)


async def main() -> None:
    args = parse_args(default_sample=20)
    golden = load_jsonl(GOLDEN_PATH)

    fast_rows = [r for r in golden if r["scenario"] in FAST_SCENARIOS]
    slow_rows = [r for r in golden if r["scenario"] not in FAST_SCENARIOS][: args.sample if not (args.full or args.all) else None]

    fast_results = []
    for row in fast_rows:
        t = row["triage"]
        got = check_pre_agent_escalation(t["intent"], t["confidence"], t["flags"])
        fast_results.append({"golden_id": row["golden_id"], "scenario": row["scenario"],
                              "expected": row["expected_reason"], "got": got, "match": got == row["expected_reason"]})
        print(f"[fast] {row['golden_id']} scenario={row['scenario']} expected={row['expected_reason']} got={got}")

    e6_results, none_results, best_effort_results = [], [], []
    pipeline_failures = []  # run_triage.py와 동일한 이유 — 한 건 실패로 전체를 죽이지 않는다
    for i, row in enumerate(slow_rows, start=1):
        try:
            final_state = await _run_full_pipeline(row)
            outcome = final_state["outcome"]
            reason = final_state.get("escalation_reason") or None
            escalated = outcome == "escalated"
            error = None
        except Exception as e:
            # [엄수] 조용히 건너뛰지 않는다 — 실패는 "에스컬레이션 미달성"으로
            # 취급해 recall 분모에는 남기고 분자를 부풀리지 않는다(run_triage.py와 동일 원칙).
            outcome, reason, escalated = "__pipeline_error__", None, False
            error = f"{type(e).__name__}: {e}"
            pipeline_failures.append({"golden_id": row["golden_id"], "scenario": row["scenario"], "error": error})

        print(f"[slow {i}/{len(slow_rows)}] {row['golden_id']} scenario={row['scenario']} "
              f"expected_escalate={row['expected_should_escalate']} -> outcome={outcome} reason={reason}"
              + (f" ERROR={error}" if error else ""))

        entry = {"golden_id": row["golden_id"], "scenario": row["scenario"], "escalated": escalated,
                  "reason": reason, "pipeline_error": error}
        if row["scenario"] == "E6":
            entry["exact_match"] = reason == "E6"
            e6_results.append(entry)
        elif row["scenario"] == "none":
            entry["false_positive"] = escalated
            none_results.append(entry)
        else:
            entry["target_reason"] = row.get("target_reason")
            best_effort_results.append(entry)

    fast_accuracy = sum(1 for r in fast_results if r["match"]) / len(fast_results) if fast_results else None
    e6_recall = sum(1 for r in e6_results if r["exact_match"]) / len(e6_results) if e6_results else None
    control_fp_rate = sum(1 for r in none_results if r["false_positive"]) / len(none_results) if none_results else None
    best_effort_recall = sum(1 for r in best_effort_results if r["escalated"]) / len(best_effort_results) if best_effort_results else None

    deterministic_recall_components = [r["match"] for r in fast_results] + [r["exact_match"] for r in e6_results]
    deterministic_recall = sum(deterministic_recall_components) / len(deterministic_recall_components) if deterministic_recall_components else None

    report = {
        "n_fast": len(fast_results), "n_slow": len(slow_rows),
        "precheck_accuracy_e1_e4": fast_accuracy,
        "e6_recall": e6_recall,
        "control_fp_rate": control_fp_rate,
        "best_effort_recall_e5_e7_e8": best_effort_recall,
        "deterministic_escalation_recall": deterministic_recall,
        "fast_results": fast_results, "e6_results": e6_results,
        "none_results": none_results, "best_effort_results": best_effort_results,
        "pipeline_failures": pipeline_failures,
        "pipeline_failure_count": len(pipeline_failures),
    }
    path = write_report("run_escalation", report)

    print(f"\nprecheck_accuracy(E1-E4)={fast_accuracy} e6_recall={e6_recall} "
          f"control_fp_rate={control_fp_rate} best_effort_recall(E5/E7/E8)={best_effort_recall}")
    print(f"결정론적 에스컬레이션 recall(E1-E4+E6 종합)={deterministic_recall}")
    print(f"파이프라인 실패(예외로 인한 미측정): {len(pipeline_failures)}건")
    print(f"리포트 저장: {path}")


if __name__ == "__main__":
    asyncio.run(main())
