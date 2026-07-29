#!/usr/bin/env python3
"""tone_golden.jsonl 기반 Judge 신뢰도 평가 — DESIGN.md 6.2절 🟡, PROMPTS.md Phase 7.

"가장 먼저 돌려야 하는" 스크립트다 — κ가 0.4 미만이면 다른 어떤 지표도
Judge 점수를 게이트로 쓰면 안 된다(PROMPTS.md Phase 7).

tone_golden의 human_tone_score가 아직 하나도 채워지지 않았다면(사용자가
아직 라벨링 전) κ를 억지로 계산하지 않고 "라벨 부족"을 명시적으로
리포트하고 종료한다 — 조용히 잘못된 숫자를 내는 것보다 훨씬 낫다.
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
from app.modules.reply.judge import judge_reply  # noqa: E402

GOLDEN_PATH = "evals/golden/tone_golden.jsonl"
KAPPA_GATE_THRESHOLD = 0.4


def _cohens_kappa(y1: list[int], y2: list[int]) -> float:
    labels = sorted(set(y1) | set(y2))
    n = len(y1)
    idx = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for a, b in zip(y1, y2):
        matrix[idx[a]][idx[b]] += 1

    po = sum(matrix[i][i] for i in range(len(labels))) / n
    row_marginals = [sum(matrix[i]) for i in range(len(labels))]
    col_marginals = [sum(matrix[i][j] for i in range(len(labels))) for j in range(len(labels))]
    pe = sum(row_marginals[i] * col_marginals[i] for i in range(len(labels))) / (n * n)

    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


async def main() -> None:
    args = parse_args(default_sample=20)
    golden = load_jsonl(GOLDEN_PATH)

    labeled = [r for r in golden if r.get("human_tone_score") is not None]
    unlabeled_count = len(golden) - len(labeled)

    if not labeled:
        report = {
            "status": "insufficient_labels",
            "n_total": len(golden), "n_labeled": 0,
            "message": (
                "tone_golden.jsonl의 human_tone_score가 전부 null입니다. "
                "사람이 직접 1~5점을 채운 뒤 다시 실행하세요. "
                "라벨 없이는 kappa를 계산하지 않습니다(PROMPTS.md Phase 7)."
            ),
        }
        path = write_report("run_judge_reliability", report)
        print("라벨 부족 — human_tone_score가 전부 null입니다. kappa를 계산하지 않고 종료합니다.")
        print(f"리포트 저장: {path}")
        return

    sample = select_sample(labeled, args)
    llm = get_judge_backend()

    human_scores, judge_scores = [], []
    rows_detail = []
    for i, row in enumerate(sample, start=1):
        judge_result = await judge_reply(
            row["ticket_text"], row["draft_text"], [], llm,
            tool_results_log=row.get("tool_results_log", []),
        )
        judge_tone = judge_result["tone"]
        human_tone = row["human_tone_score"]
        human_scores.append(human_tone)
        judge_scores.append(judge_tone)
        rows_detail.append({"golden_id": row.get("golden_id"), "human": human_tone, "judge": judge_tone})
        print(f"[{i}/{len(sample)}] {row.get('golden_id')} human={human_tone} judge={judge_tone}")

    kappa = _cohens_kappa(human_scores, judge_scores)
    within_one = sum(1 for h, j in zip(human_scores, judge_scores) if abs(h - j) <= 1) / len(sample)

    report = {
        "status": "measured",
        "n_total": len(golden), "n_labeled": len(labeled), "n_used": len(sample),
        "n_still_unlabeled": unlabeled_count,
        "cohens_kappa": kappa,
        "within_one_agreement_rate": within_one,
        "gate_threshold": KAPPA_GATE_THRESHOLD,
        "trustworthy": kappa >= KAPPA_GATE_THRESHOLD,
        "rows": rows_detail,
    }
    path = write_report("run_judge_reliability", report)

    print(f"\ncohens_kappa={kappa:.3f} (게이트 임계값 {KAPPA_GATE_THRESHOLD}) "
          f"within_one_agreement={within_one:.3f}")
    if kappa < KAPPA_GATE_THRESHOLD:
        print("경고: κ가 임계값 미달입니다 — 다른 어떤 지표도 이 Judge 점수를 게이트로 쓰지 마세요.")
    print(f"리포트 저장: {path}")


if __name__ == "__main__":
    asyncio.run(main())
