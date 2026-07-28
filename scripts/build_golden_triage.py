#!/usr/bin/env python3
"""triage_golden.jsonl 생성 — DESIGN.md 6.3절.

Bitext 원본에서 하이드레이션 때 이미 붙은 intent/category를 그대로 정답으로
쓴다(새로 라벨링하지 않음) — 인텐트당 약 7~8건 층화 샘플링, 인텐트 내에서는
flags 조합이 다양한 순서로 섞이도록 셔플 후 앞에서부터 뽑는다.

evals/golden/은 보호 경로라 이 스크립트의 출력은 Bash 실행으로만 만든다
(CLAUDE.md ★ 보호 경로 — Write/Edit 도구로 직접 쓰지 않는다).
"""
import collections
import json
import pathlib
import random

SEED = 42
TARGET_TOTAL = 200
TICKETS_PATH = pathlib.Path("data/synthetic/tickets.jsonl")
OUTPUT_PATH = pathlib.Path("evals/golden/triage_golden.jsonl")


def main() -> None:
    if OUTPUT_PATH.exists():
        print(f"이미 존재함, 건너뜀: {OUTPUT_PATH}")
        return

    by_intent = collections.defaultdict(list)
    with open(TICKETS_PATH, encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            by_intent[t["intent"]].append(t)

    intents = sorted(by_intent)
    n_intents = len(intents)
    base = TARGET_TOTAL // n_intents
    remainder = TARGET_TOTAL - base * n_intents

    rng = random.Random(SEED)
    rows = []
    for i, intent in enumerate(intents):
        quota = base + (1 if i < remainder else 0)
        pool = by_intent[intent]
        rng.shuffle(pool)
        for t in pool[:quota]:
            rows.append({
                "ticket_id": t["ticket_id"],
                "text": t["text"],
                "intent": t["intent"],
                "category": t["category"],
                "flags": t["flags"],
            })

    rng.shuffle(rows)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"완료: {OUTPUT_PATH} — {len(rows)}건, 인텐트 {n_intents}종")


if __name__ == "__main__":
    main()
