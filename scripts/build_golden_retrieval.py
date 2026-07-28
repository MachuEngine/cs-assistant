#!/usr/bin/env python3
"""retrieval_golden.jsonl 생성 — DESIGN.md 6.3절.

합성 정책 문서(data/synthetic/policies/*.md) 28개 조항 ID에 대해 자연어
질의를 직접 작성해 query -> clause_id 매핑을 만든다. 대부분은 조항 하나당
질의 하나지만, 두 조항에 걸치는 질의(복수 정답 허용) 몇 개를 추가해 30건을
채운다.

evals/golden/은 보호 경로라 이 스크립트의 출력은 Bash 실행으로만 만든다.
"""
import json
import pathlib

OUTPUT_PATH = pathlib.Path("evals/golden/retrieval_golden.jsonl")

ROWS = [
    ("If my order hasn't started processing yet, is there a fee to cancel it?", ["CANC-01"]),
    ("What's the cancellation fee once my order is already being processed?", ["CANC-02"]),
    ("Can I cancel an order that has already shipped?", ["CANC-03"]),
    ("Can I still modify the items in my order after placing it?", ["CANC-04"]),
    ("What are the different membership tiers you offer?", ["TIER-01"]),
    ("What benefits does the Standard tier include?", ["TIER-02"]),
    ("What benefits come with the Plus membership tier?", ["TIER-03"]),
    ("What perks does the VIP tier give me?", ["TIER-04"]),
    ("What payment methods do you accept?", ["PAY-01"]),
    ("My card payment failed, what should I do?", ["PAY-02"]),
    ("Can I pay using store credit or a gift card?", ["PAY-03"]),
    ("How will my refund be paid back to me?", ["REF-01"]),
    ("How long does a refund take to process for my membership tier?", ["REF-02"]),
    ("Can I get a partial refund for only part of my order?", ["REF-03"]),
    ("Do I get a refund if I cancel before the order ships?", ["REF-04"]),
    ("How will I know once my refund has actually gone through?", ["REF-05"]),
    ("What are the general rules for being eligible to return an item?", ["RET-01"]),
    ("How many days do I have to return an item based on my tier?", ["RET-02"]),
    ("Are there items that just can't be returned at all?", ["RET-03"]),
    ("How does the exchange process work if I want a different size?", ["RET-04"]),
    ("Do I have to pay for return shipping myself?", ["RET-05"]),
    ("What condition does a returned item need to be in?", ["RET-06"]),
    ("How long does standard delivery normally take?", ["SHIP-01"]),
    ("Is expedited shipping faster depending on my membership tier?", ["SHIP-02"]),
    ("Can I change the shipping address after I've placed the order?", ["SHIP-03"]),
    ("My package is delayed, what happens next?", ["SHIP-04"]),
    ("Are there countries you can't ship international orders to?", ["SHIP-05"]),
    ("What does the standard warranty cover on my product?", ["WARR-01"]),
    ("Can I get extended warranty coverage based on my tier?", ["WARR-02"]),
    ("How do I actually file a warranty claim?", ["WARR-03"]),
    ("If I cancel an order that hasn't shipped yet, do I get charged a fee and do I get a refund?", ["CANC-01", "REF-04"]),
    ("Does my membership tier affect both how fast my refund arrives and how long I have to return something?", ["REF-02", "RET-02"]),
]


def main() -> None:
    if OUTPUT_PATH.exists():
        print(f"이미 존재함, 건너뜀: {OUTPUT_PATH}")
        return

    rows = []
    for i, (query, clause_ids) in enumerate(ROWS, start=1):
        rows.append({
            "golden_id": f"RTV-{i:03d}",
            "query": query,
            "expected_clause_ids": clause_ids,
        })

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"완료: {OUTPUT_PATH} — {len(rows)}건")


if __name__ == "__main__":
    main()
