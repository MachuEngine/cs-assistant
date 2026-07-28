#!/usr/bin/env python3
"""pii_golden.jsonl 생성 — DESIGN.md 6.3절.

Bitext는 이미 익명화돼 있어 마스킹할 실제 PII가 없다 — 이 스크립트가 합성
영문 PII를 하이드레이션된 티켓 본문에 직접 주입하고, 주입한 정확한
문자열·타입·오프셋을 정답으로 기록한다. 삽입 시점에 정답을 이미 알고
있으므로(내가 만든 값이므로) 사람 판단이 필요 없다 — pii_golden/
policy_violation_golden은 tone_golden과 성격이 다르다.

이름(NAME) 타입은 일부러 app.common.privacy의 가제티어에 없는 이름도
섞는다 — 전부 가제티어 안 이름만 넣으면 실제 FN율을 과소평가(자기 자신을
속이는 골든셋)하게 된다. mask_pii의 알려진 한계(재현율<100%)를 골든셋도
정직하게 반영해야 한다.

evals/golden/은 보호 경로라 이 스크립트의 출력은 Bash 실행으로만 만든다.
"""
import json
import pathlib
import random

TICKETS_PATH = pathlib.Path("data/synthetic/tickets.jsonl")
OUTPUT_PATH = pathlib.Path("evals/golden/pii_golden.jsonl")
SEED = 42
N_PER_TYPE = 10

# app.common.privacy 가제티어 안 이름 (마스킹돼야 정상)
GAZETTEER_NAMES = [
    ("John", "Smith"), ("Jane", "Jones"), ("Michael", "Davis"),
    ("Sarah", "Wilson"), ("David", "Brown"), ("Karen", "Miller"),
    ("Robert", "Garcia"), ("Susan", "Martinez"),
]
# 가제티어 밖 이름 — 잡히지 않는 게 "정답"인 알려진 한계 케이스
NON_GAZETTEER_NAMES = [("Yuki", "Tanaka"), ("Fatima", "Hassan")]

ADDRESSES = [
    "123 Main Street", "456 Oak Avenue", "789 Elm Road", "12 Maple Drive",
    "88 Birch Lane", "301 Pine Court", "55 Cedar Place", "900 Willow Way",
    "17 Spruce Terrace", "640 Aspen Boulevard",
]

# 자릿수 사이 하이픈/공백 없이 순수 숫자 13~19자리, Luhn 유효
CARD_NUMBERS = [
    "4111111111111111", "5500000000000004", "340000000000009",
    "6011000000000004", "3530111333300000", "4012888888881881",
    "4222222222222", "5105105105105100", "4000056655665556",
    "3566002020360505",
]

PHONE_NUMBERS = [
    "555-123-4567", "(555) 234-5678", "+1 555 345 6789", "555.456.7890",
    "555-567-8901", "(555) 678-9012", "+1 555 789 0123", "555.890.1234",
    "555-901-2345", "(555) 012-3456",
]


def _inject(text: str, value: str) -> tuple[str, int, int]:
    """문장 끝에 자연스럽게 이어붙이고, 삽입된 정확한 오프셋을 반환한다."""
    sep = " " if not text.endswith((".", "!", "?")) else " "
    prefix = f"{text.rstrip()}{sep}Contact detail: "
    start = len(prefix)
    new_text = f"{prefix}{value}."
    return new_text, start, start + len(value)


def main() -> None:
    if OUTPUT_PATH.exists():
        print(f"이미 존재함, 건너뜀: {OUTPUT_PATH}")
        return

    tickets = []
    with open(TICKETS_PATH, encoding="utf-8") as f:
        for line in f:
            tickets.append(json.loads(line))

    rng = random.Random(SEED)
    rng.shuffle(tickets)
    pool = iter(tickets)

    rows = []

    for email_local in (f"customer{i}" for i in range(N_PER_TYPE)):
        t = next(pool)
        value = f"{email_local}@example.com"
        text, start, end = _inject(t["text"], value)
        rows.append({
            "ticket_id": t["ticket_id"], "text": text,
            "pii_type": "EMAIL", "pii_value": value,
            "start": start, "end": end, "should_be_masked": True,
        })

    for value in PHONE_NUMBERS:
        t = next(pool)
        text, start, end = _inject(t["text"], value)
        rows.append({
            "ticket_id": t["ticket_id"], "text": text,
            "pii_type": "PHONE", "pii_value": value,
            "start": start, "end": end, "should_be_masked": True,
        })

    for value in CARD_NUMBERS:
        t = next(pool)
        text, start, end = _inject(t["text"], value)
        rows.append({
            "ticket_id": t["ticket_id"], "text": text,
            "pii_type": "CARD", "pii_value": value,
            "start": start, "end": end, "should_be_masked": True,
        })

    for value in ADDRESSES:
        t = next(pool)
        text, start, end = _inject(t["text"], value)
        rows.append({
            "ticket_id": t["ticket_id"], "text": text,
            "pii_type": "ADDRESS", "pii_value": value,
            "start": start, "end": end, "should_be_masked": True,
        })

    for first, last in GAZETTEER_NAMES:
        t = next(pool)
        value = f"{first} {last}"
        text, start, end = _inject(t["text"], value)
        rows.append({
            "ticket_id": t["ticket_id"], "text": text,
            "pii_type": "NAME", "pii_value": value,
            "start": start, "end": end, "should_be_masked": True,
        })
    for first, last in NON_GAZETTEER_NAMES:
        t = next(pool)
        value = f"{first} {last}"
        text, start, end = _inject(t["text"], value)
        rows.append({
            "ticket_id": t["ticket_id"], "text": text,
            "pii_type": "NAME", "pii_value": value,
            "start": start, "end": end,
            # 가제티어 밖 이름 — mask_pii의 알려진 한계로 놓치는 게 "정상"
            "should_be_masked": False,
        })

    rng.shuffle(rows)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"완료: {OUTPUT_PATH} — {len(rows)}건 (가제티어 밖 이름 {len(NON_GAZETTEER_NAMES)}건 포함)")


if __name__ == "__main__":
    main()
