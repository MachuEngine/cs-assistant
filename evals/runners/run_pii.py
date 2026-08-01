#!/usr/bin/env python3
"""pii_golden.jsonl 기반 마스킹 평가 — DESIGN.md 6.2절 🔴 FN율(목표 0), FP율.

LLM 호출 없음(mask_pii는 순수 함수) — --full로 돌려도 비용이 없다.

pii_golden 중 should_be_masked=False인 행은 두 그룹으로 나뉜다:
- `fp_case: true` — 진짜 비-PII인데 마스킹 정규식과 우연히 모양이 겹치는
  케이스(Luhn-valid한 비카드 참조번호, "N x Flash Drive" 같은 ADDRESS 오탐,
  가제티어 이름 조합과 겹치는 장소/브랜드명 등). 이 그룹으로 fp_rate를 잰다
  (2026-08-01 추가, NEXT_STEPS.md 우선순위 4 — 그 전까지는 이런 대조군이
  golden에 없어 fp_rate를 정직하게 None으로만 리포트했다).
- `fp_case` 없음(또는 false) — 가제티어 밖 이름이라 애초에 못 잡는 걸 아는
  기존 "known_limitation" 그룹. FN 분모에서 제외하고 별도 리포트한다(기존
  동작 그대로, fp_rate 계산에는 포함하지 않는다 — 이건 recall 한계지 정밀도
  문제가 아니다).

EMAIL 타입은 fp_case가 없다 — 정규식이 "word@word.tld" 모양을 엄격히
요구해 자연스러운 비-PII 오탐 사례를 구성하기 어렵다(알려진 한계로만 남김).
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
    fp_rows = [r for r in sample if r.get("fp_case")]
    known_limitation_rows = [r for r in sample if not r["should_be_masked"] and not r.get("fp_case")]

    fn_cases = []
    for row in target_rows:
        masked = mask_pii(row["text"])
        if row["pii_value"] in masked:
            fn_cases.append({"golden_id": row.get("golden_id"), "pii_type": row["pii_type"], "pii_value": row["pii_value"]})

    fp_cases = []
    for row in fp_rows:
        masked = mask_pii(row["text"])
        if row["pii_value"] not in masked:
            fp_cases.append({"golden_id": row.get("golden_id"), "pii_type": row["pii_type"], "pii_value": row["pii_value"]})

    known_limitation_caught = []
    for row in known_limitation_rows:
        masked = mask_pii(row["text"])
        if row["pii_value"] not in masked:
            known_limitation_caught.append(row.get("golden_id"))

    fn_rate = len(fn_cases) / len(target_rows) if target_rows else None
    fp_rate = len(fp_cases) / len(fp_rows) if fp_rows else None

    report = {
        "n": len(sample),
        "n_target": len(target_rows),
        "n_fp_target": len(fp_rows),
        "n_known_limitation": len(known_limitation_rows),
        "fn_rate": fn_rate,
        "fn_cases": fn_cases,
        "fp_rate": fp_rate,
        "fp_cases": fp_cases,
        "known_limitation_caught_anyway": known_limitation_caught,
    }
    path = write_report("run_pii", report)

    print(f"n_target={len(target_rows)} fn_rate={fn_rate} (목표 0)")
    print(f"n_fp_target={len(fp_rows)} fp_rate={fp_rate} (목표 0)")
    print(f"known_limitation 그룹({len(known_limitation_rows)}건)은 FN 분모에서 제외됨")
    if fn_cases:
        print(f"FN 발생: {fn_cases}")
    if fp_cases:
        print(f"FP 발생: {fp_cases}")
    print(f"리포트 저장: {path}")


if __name__ == "__main__":
    main()
