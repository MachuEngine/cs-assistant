#!/usr/bin/env python3
"""evals/reports/*.json을 DESIGN.md 6.1/6.2절 임계값과 비교 — 미달 시 exit 1.

이 파일은 보호 경로다(CLAUDE.md ★). 여기 적힌 임계값은 DESIGN.md에 이미
문서화된 통과 기준(시작값)이며, eval이 실패한다고 여기서 낮춰서 통과시키지
않는다 — 실패하면 app/의 로직을 고친다.

톤 평균(≥4.0)과 도구호출수·latency(🟢 과정 지표)는 이 스모크 러너들이 아직
다루지 않는다 — tone_golden은 κ 측정용으로 소규모·선별된 표본이라 "전체
초안 코퍼스의 평균 톤"을 대표하지 못한다. 이건 --full 단계(사람이 직접
실행하는 더 큰 실제 배치)에서 별도로 측정해야 하는 알려진 공백이다
(EVAL.md에 명시).
"""
import json
import pathlib

REPORTS_DIR = pathlib.Path("evals/reports")

# (리포트 파일명, 지표 키, 연산자, 임계값, 출처)
GATES = [
    ("run_triage", "intent_accuracy", ">=", 0.85, "DESIGN.md 6.1"),
    ("run_triage", "intent_macro_f1", ">=", 0.80, "DESIGN.md 6.1"),
    ("run_triage", "category_accuracy", ">=", 0.92, "DESIGN.md 6.1"),
    ("run_pii", "fn_rate", "==", 0.0, "DESIGN.md 6.2 🔴"),
    ("run_policy_violation", "judge_overall_recall", ">=", 0.95, "DESIGN.md 6.2 🔴"),
    ("run_policy_violation", "gate_recall_for_covered_types", ">=", 1.0, "DESIGN.md 6.2 🔴 (근거없는 확약률 0)"),
    ("run_escalation", "deterministic_escalation_recall", ">=", 0.90, "DESIGN.md 6.2 ⭐"),
    ("run_retrieval", "recall_at_5_partial", ">=", 0.80, "DESIGN.md 6.2 🟢"),
    ("run_judge_reliability", "cohens_kappa", ">=", 0.40, "DESIGN.md 6.2 🟡 / PROMPTS.md Phase 7"),
]

_OPS = {
    ">=": lambda v, t: v >= t,
    "==": lambda v, t: v == t,
}


def main() -> int:
    all_pass = True
    print(f"{'리포트':<24}{'지표':<32}{'값':<12}{'기준':<10}판정")
    print("-" * 90)

    for report_name, key, op, threshold, source in GATES:
        path = REPORTS_DIR / f"{report_name}.json"
        if not path.exists():
            print(f"{report_name:<24}{key:<32}{'N/A':<12}{'':<10}NOT_RUN ({source})")
            continue

        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") == "insufficient_labels":
            print(f"{report_name:<24}{key:<32}{'N/A':<12}{'':<10}PENDING (라벨 부족)")
            continue

        value = data.get(key)
        if value is None:
            print(f"{report_name:<24}{key:<32}{'N/A':<12}{'':<10}NOT_MEASURED")
            continue

        passed = _OPS[op](value, threshold)
        all_pass = all_pass and passed
        verdict = "PASS" if passed else "FAIL"
        print(f"{report_name:<24}{key:<32}{value:<12.3f}{op}{threshold:<9}{verdict}")

    print("-" * 90)
    if all_pass:
        print("모든 측정된 게이트 통과.")
        return 0
    print("하나 이상의 게이트 미달. evals/golden/의 임계값을 낮추지 말고 app/의 로직을 고치세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
