#!/usr/bin/env python3
"""policy_violation_golden.jsonl 기반 위반 검출 평가 — DESIGN.md 6.2절 🔴.

policy_contradiction/out_of_scope_promise는 어떤 결정론적 게이트도 못
잡는다(judge_reply()의 violations 스키마에만 있는 유형) — 이 지표
자체가 "Judge를 믿을 수 있는가"를 재는 것이라 judge_reply()를 직접
호출해서 측정한다(게이트를 우회해 재는 게 아니라, 게이트가 원래 못
잡는 부분을 재는 것).

unsupported_commitment(금액형)/missing_citation은 app.modules.reply.tools의
게이트②/④로도 결정론적으로 잡혀야 하므로 save_draft를 직접 호출해
동일 문항에 대한 게이트 자체 recall도 보조로 리포트한다(LLM 호출 없음,
빠름) — 이건 게이트 회귀 확인용이지 이 eval의 주 지표가 아니다.

[2026-08-01 추가] 골든셋에 위반 없는 대조군(violation_type=null, PV-051~059)을
9건 추가해 precision/F1을 계산할 수 있게 됐다 — 전에는 양성 예시만 있어
recall만 측정 가능했다(NEXT_STEPS.md 우선순위 4). 정의:
- TP = 양성 행에서 judge가 골든이 심어둔 정확한 violation_type을 찾음(기존
  judge_overall_recall과 동일 분자)
- FN = 양성 행에서 못 찾음
- FP = 대조군 행(원래 위반 없음)인데 judge가 아무 violation이라도 보고함
- precision = TP/(TP+FP), recall = TP/(TP+FN)(=기존 judge_overall_recall과 동일),
  F1 = 조화평균

[2026-08-01 수정] run_triage.py에서 실측된 것과 같은 결함 — judge_reply()
호출(LLM)이 실패를 못 잡아서 --full 도중 한 건만 실패해도 전체가 죽고
그때까지의 API 비용이 리포트 없이 날아갔다. 한 건의 judge 실패를 해당
유형의 recall 분모에는 남기고(놓친 것으로 취급) 분자는 안 채우도록
고친다 — 조용히 빼서 recall을 부풀리지 않는다(run_triage.py와 동일 원칙).
대조군 행에서 judge 호출이 실패하면 "위반 미검출"과 결과적으로 동일하게
처리되는데(found_types가 빈 집합으로 남음), 이는 대조군에서는 우연히
정답과 같은 방향이라 precision을 부풀리지 않는다 — 다만 judge_failures에는
항상 기록해 원인 진단 시 조용히 묻히지 않게 한다.
"""
import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

os.environ.setdefault("JUDGE_BACKEND", "ollama")
os.environ.setdefault("OLLAMA_MODEL", "qwen2.5:14b")

from _common import load_jsonl, parse_args, select_sample, write_report  # noqa: E402
from app.common.llm import get_judge_backend  # noqa: E402
from app.modules.reply import tools as reply_tools  # noqa: E402
from app.modules.reply.judge import judge_reply  # noqa: E402

GOLDEN_PATH = "evals/golden/policy_violation_golden.jsonl"


def _gate_check(row: dict) -> bool | None:
    """게이트②/④가 대응하는 유형만 직접 save_draft로 재확인한다(보조 지표).
    대조군(violation_type=None)은 애초에 이 두 유형에 안 걸리니 자연히 스킵된다."""
    if row["violation_type"] not in ("unsupported_commitment", "missing_citation"):
        return None
    reply_tools.bind_session()
    reply_tools.init_session(ticket_text=row["ticket_text"], order_id="", intent=row["intent"])
    ctx = reply_tools.get_ctx()
    ctx["tool_results_log"] = list(row.get("tool_results_log", []))
    result = reply_tools.save_draft.invoke({"reply_text": row["draft_text"]})
    return result.startswith("Rejected")


async def main() -> None:
    args = parse_args(default_sample=20)
    golden = load_jsonl(GOLDEN_PATH)
    sample = select_sample(golden, args)
    llm = get_judge_backend()

    per_type_total: dict[str, int] = {}
    per_type_hit: dict[str, int] = {}
    gate_total, gate_hit = 0, 0
    misses = []
    judge_failures = []  # run_triage.py와 동일한 이유 — 한 건 실패로 전체를 죽이지 않는다

    clean_total, clean_fp = 0, 0
    fp_cases = []

    for i, row in enumerate(sample, start=1):
        is_clean = row["violation_type"] is None
        if not is_clean:
            per_type_total[row["violation_type"]] = per_type_total.get(row["violation_type"], 0) + 1

        try:
            judge_result = await judge_reply(
                row["ticket_text"], row["draft_text"], [], llm,
                tool_results_log=row.get("tool_results_log", []),
            )
            found_types = {v.get("type") for v in judge_result.get("violations", [])}
            error = None
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            found_types = set()
            judge_failures.append({"golden_id": row.get("golden_id"), "violation_type": row["violation_type"],
                                    "error": error})

        if is_clean:
            clean_total += 1
            hit = not found_types  # 대조군은 "아무것도 못 찾음"이 정답
            if found_types:
                clean_fp += 1
                fp_cases.append({"golden_id": row.get("golden_id"), "judge_violations": list(found_types)})
        else:
            hit = row["violation_type"] in found_types
            if hit:
                per_type_hit[row["violation_type"]] = per_type_hit.get(row["violation_type"], 0) + 1
            else:
                misses.append({"golden_id": row.get("golden_id"), "violation_type": row["violation_type"],
                                "judge_violations": list(found_types), "judge_error": error})

        gate_result = _gate_check(row)
        if gate_result is not None:
            gate_total += 1
            gate_hit += int(gate_result)

        print(f"[{i}/{len(sample)}] {row.get('golden_id')} type={row['violation_type']} "
              f"judge_hit={hit} gate_hit={gate_result}" + (f" ERROR={error}" if error else ""))

    overall_recall = sum(per_type_hit.values()) / sum(per_type_total.values()) if per_type_total else None
    per_type_recall = {
        t: per_type_hit.get(t, 0) / per_type_total[t] for t in per_type_total
    }
    gate_recall = gate_hit / gate_total if gate_total else None

    tp = sum(per_type_hit.values())
    fp = clean_fp
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = overall_recall
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and (precision + recall) > 0 else None)
    control_fp_rate = clean_fp / clean_total if clean_total else None

    report = {
        "n": len(sample),
        "judge_overall_recall": overall_recall,
        "judge_per_type_recall": per_type_recall,
        "judge_precision": precision,
        "judge_f1": f1,
        "n_clean_control": clean_total,
        "control_fp_rate": control_fp_rate,
        "control_fp_cases": fp_cases,
        "gate_recall_for_covered_types": gate_recall,
        "misses": misses,
        "judge_failures": judge_failures,
        "judge_failure_count": len(judge_failures),
    }
    path = write_report("run_policy_violation", report)

    print(f"\njudge_overall_recall={overall_recall} per_type={per_type_recall}")
    print(f"judge_precision={precision} judge_f1={f1} (대조군 {clean_total}건, control_fp_rate={control_fp_rate})")
    print(f"gate_recall(②/④ 커버 유형만)={gate_recall}")
    if judge_failures:
        print(f"judge_reply() 실패(recall 분모에 남기고 미스로 처리): {len(judge_failures)}건")
    print(f"리포트 저장: {path}")


if __name__ == "__main__":
    asyncio.run(main())
