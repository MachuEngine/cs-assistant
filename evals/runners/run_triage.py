#!/usr/bin/env python3
"""triage_golden.jsonl 기반 triage 모듈 평가 — DESIGN.md 6.1절.

인텐트 accuracy·macro-F1·카테고리 accuracy·confidence 캘리브레이션·
혼동행렬(인접 인텐트 쌍 국소 붕괴 탐지용, DESIGN.md 6.1절 각주)을 계산한다.

app.modules.triage.classifier.triage_ticket()을 실제로 호출한다(고정
출력을 채점하지 않는다 — CLAUDE.md 평가 규칙).
"""
import asyncio
import collections
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

os.environ.setdefault("LLM_BACKEND", "ollama")
os.environ.setdefault("OLLAMA_MODEL", "qwen2.5:14b")

from _common import load_jsonl, parse_args, select_sample, write_report  # noqa: E402
from app.modules.triage.classifier import triage_ticket  # noqa: E402

GOLDEN_PATH = "evals/golden/triage_golden.jsonl"

# DESIGN.md 6.1절이 짚은, 의미가 인접해 헷갈리기 쉬운 인텐트 쌍
CONFUSING_PAIRS = [
    ("check_invoice", "get_invoice"),
    ("check_refund_policy", "get_refund"),
    ("change_shipping_address", "set_up_shipping_address"),
]


def _macro_f1(y_true: list[str], y_pred: list[str]) -> tuple[float, dict]:
    labels = sorted(set(y_true) | set(y_pred))
    per_label = {}
    f1_sum = 0.0
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_label[label] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
        f1_sum += f1
    macro_f1 = f1_sum / len(labels) if labels else 0.0
    return macro_f1, per_label


async def main() -> None:
    args = parse_args(default_sample=20)
    golden = load_jsonl(GOLDEN_PATH)
    sample = select_sample(golden, args)

    y_true_intent, y_pred_intent = [], []
    y_true_cat, y_pred_cat = [], []
    confidences_correct, confidences_wrong = [], []
    confusion = collections.Counter()
    errors = []

    for i, row in enumerate(sample, start=1):
        result = await triage_ticket(row["text"], row.get("flags", ""))
        y_true_intent.append(row["intent"])
        y_pred_intent.append(result["intent"])
        y_true_cat.append(row["category"])
        y_pred_cat.append(result["category"])

        correct = result["intent"] == row["intent"]
        (confidences_correct if correct else confidences_wrong).append(result["confidence"])
        if not correct:
            confusion[(row["intent"], result["intent"])] += 1
            errors.append({
                "ticket_id": row["ticket_id"], "true_intent": row["intent"],
                "pred_intent": result["intent"], "confidence": result["confidence"],
            })
        print(f"[{i}/{len(sample)}] {row['ticket_id']} true={row['intent']!r} "
              f"pred={result['intent']!r} conf={result['confidence']:.2f} {'OK' if correct else 'MISS'}")

    n = len(sample)
    intent_acc = sum(1 for t, p in zip(y_true_intent, y_pred_intent) if t == p) / n if n else 0.0
    cat_acc = sum(1 for t, p in zip(y_true_cat, y_pred_cat) if t == p) / n if n else 0.0
    macro_f1, per_label = _macro_f1(y_true_intent, y_pred_intent)

    adjacent_pair_confusions = {
        f"{a}<->{b}": confusion.get((a, b), 0) + confusion.get((b, a), 0)
        for a, b in CONFUSING_PAIRS
    }

    avg_conf_correct = sum(confidences_correct) / len(confidences_correct) if confidences_correct else None
    avg_conf_wrong = sum(confidences_wrong) / len(confidences_wrong) if confidences_wrong else None

    report = {
        "n": n,
        "intent_accuracy": intent_acc,
        "intent_macro_f1": macro_f1,
        "category_accuracy": cat_acc,
        "confidence_calibration": {
            "avg_confidence_correct": avg_conf_correct,
            "avg_confidence_wrong": avg_conf_wrong,
            "calibrated": (avg_conf_correct or 0) > (avg_conf_wrong or 0) if confidences_wrong else None,
        },
        "adjacent_pair_confusions": adjacent_pair_confusions,
        "confusion_matrix": {f"{t}->{p}": c for (t, p), c in confusion.items()},
        "per_intent": per_label,
        "errors": errors,
    }
    path = write_report("run_triage", report)

    print(f"\nintent_accuracy={intent_acc:.3f} macro_f1={macro_f1:.3f} category_accuracy={cat_acc:.3f}")
    print(f"confidence(correct)={avg_conf_correct} confidence(wrong)={avg_conf_wrong}")
    print(f"인접 인텐트 쌍 혼동: {adjacent_pair_confusions}")
    print(f"리포트 저장: {path}")


if __name__ == "__main__":
    asyncio.run(main())
