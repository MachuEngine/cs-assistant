#!/usr/bin/env python3
"""pii_golden.jsonl 기반 마스킹 평가 — DESIGN.md 6.2절 🔴 FN율(목표 0).

LLM 호출 없음(mask_pii는 순수 함수) — --full로 돌려도 비용이 없다.

pii_golden 중 should_be_masked=False인 몇 건(가제티어 밖 이름)은 mask_pii의
알려진 한계를 정직하게 반영한 것이라 FN 분모에서 제외하고 별도
"known_limitation" 그룹으로 리포트한다.

FP(비-PII를 잘못 마스킹)는 현재 골든셋에 "마스킹되면 안 되는 문구"
control 케이스가 없어 측정할 수 없다 — 정직하게 None으로 리포트한다
(측정 안 된 걸 0으로 위장하지 않는다).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _common import load_jsonl, parse_args, select_sample, write_report  # noqa: E402
from app.common.privacy import mask_pii  # noqa: E402

GOLDEN_PATH = "evals/golden/pii_golden.jsonl"


def main() -> None:
    args = parse_args(default_sample=20)
    golden = load_jsonl(GOLDEN_PATH)
    sample = select_sample(golden, args)

    target_rows = [r for r in sample if r["should_be_masked"]]
    known_limitation_rows = [r for r in sample if not r["should_be_masked"]]

    fn_cases = []
    for row in target_rows:
        masked = mask_pii(row["text"])
        if row["pii_value"] in masked:
            fn_cases.append({"golden_id": row.get("golden_id"), "pii_type": row["pii_type"], "pii_value": row["pii_value"]})

    known_limitation_caught = []
    for row in known_limitation_rows:
        masked = mask_pii(row["text"])
        if row["pii_value"] not in masked:
            known_limitation_caught.append(row.get("golden_id"))

    fn_rate = len(fn_cases) / len(target_rows) if target_rows else None

    report = {
        "n": len(sample),
        "n_target": len(target_rows),
        "n_known_limitation": len(known_limitation_rows),
        "fn_rate": fn_rate,
        "fn_cases": fn_cases,
        "known_limitation_caught_anyway": known_limitation_caught,
        "fp_rate": None,  # 측정 불가 — 위 docstring 참고
    }
    path = write_report("run_pii", report)

    print(f"n_target={len(target_rows)} fn_rate={fn_rate} (목표 0)")
    print(f"known_limitation 그룹({len(known_limitation_rows)}건)은 FN 분모에서 제외됨")
    if fn_cases:
        print(f"FN 발생: {fn_cases}")
    print(f"리포트 저장: {path}")


if __name__ == "__main__":
    main()
