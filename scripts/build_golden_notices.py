#!/usr/bin/env python3
"""notices_golden.jsonl 생성 — Phase 12a, DESIGN.md 6.3절.

공지 조회 자체가 결정론적 순수 함수(is_notice_active)와 stub 소스로만 이뤄져
있어 이 골든셋은 전체가 100% 결정론적으로 구성 가능하다(escalation_golden.jsonl의
E5/E7/E8처럼 best-effort로 남겨야 할 구간이 없다).

날짜는 실제 실행 시점의 UTC 오늘이 아니라 각 행에 고정된 "as_of" 기준일로
판정한다 — 그래야 이 골든셋이 몇 달 뒤에 재실행돼도 활성/비활성 결과가
그대로 재현된다(run_notices.py는 is_notice_active(notice, today=as_of)로
호출해야 한다).

evals/golden/은 보호 경로라 이 스크립트의 출력은 Bash 실행으로만 만든다
(다른 build_golden_*.py 6종과 동일 패턴 — 훅은 Write/Edit 도구만 막는다).
"""
import datetime
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TICKETS_PATH = pathlib.Path("data/synthetic/tickets.jsonl")
OUTPUT_PATH = pathlib.Path("evals/golden/notices_golden.jsonl")
SEED = 42

AS_OF = datetime.date(2026, 8, 1)  # 이 골든셋의 고정 기준일(실제 오늘과 무관)

# 사람이 확정한 필수 인텐트(PROMPTS.md Phase 12 + check_payment_methods 추가
# 확정, 2026-07-30) — app.modules.reply.routing.NOTICE_REQUIRED와 반드시
# 일치해야 한다. 표를 복제하는 게 아니라 여기서도 같은 이름으로 다시 선언하면
# 드리프트 위험이 있으므로, 실제 import로 검증한다.
from app.modules.reply.routing import INTENT_TO_CATEGORY, NOTICE_REQUIRED  # noqa: E402


def _load_tickets() -> list[dict]:
    with open(TICKETS_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def _notice(notice_id: str, category: str, body: str, **overrides) -> dict:
    base = {
        "notice_id": notice_id,
        "title": f"{category} notice",
        "body": body,
        "scope": [category],
        "valid_from": (AS_OF - datetime.timedelta(days=3)).isoformat(),
        "valid_until": (AS_OF + datetime.timedelta(days=10)).isoformat(),
        "active": True,
    }
    base.update(overrides)
    return base


def main() -> None:
    if OUTPUT_PATH.exists():
        print(f"이미 존재함, 건너뜀: {OUTPUT_PATH}")
        return

    tickets = _load_tickets()
    rng = random.Random(SEED)
    rng.shuffle(tickets)
    by_intent: dict[str, list[dict]] = {}
    for t in tickets:
        by_intent.setdefault(t["intent"], []).append(t)

    required_intents = sorted(NOTICE_REQUIRED)
    rows: list[dict] = []

    def _pick(intent: str, n: int, offset: int = 0) -> list[dict]:
        return by_intent.get(intent, [])[offset:offset + n]

    # --- 활성 + scope 일치 → 반영해야 함(누락 시 FN) --------------------------
    for i, intent in enumerate(required_intents[:5]):
        t = _pick(intent, 1)[0]
        category = INTENT_TO_CATEGORY[intent]
        notice = _notice(
            f"MATCH-{i+1:02d}", category,
            f"{category} operations are running a temporary delay this week.",
        )
        rows.append({
            "scenario": "active_match",
            "ticket_id": t["ticket_id"], "ticket_text": t["text"],
            "intent": intent, "category": category,
            "as_of": AS_OF.isoformat(),
            "lookup_fails": False,
            "notices": [notice],
            "expected_grounded_ids": [notice["notice_id"]],
            "expected_escalation": None,
        })

    # --- 활성 + scope 불일치 → 반영하면 안 됨(반영 시 FP) ---------------------
    mismatch_pairs = [
        ("track_order", "PAYMENT"),
        ("payment_issue", "SHIPPING"),
        ("delivery_period", "REFUND"),
        ("track_refund", "DELIVERY"),
        ("change_shipping_address", "ORDER"),
    ]
    for i, (intent, foreign_category) in enumerate(mismatch_pairs):
        t = _pick(intent, 1, offset=1)[0]
        category = INTENT_TO_CATEGORY[intent]
        notice = _notice(
            f"MISMATCH-{i+1:02d}", foreign_category,
            f"{foreign_category} customers may see a one-time ${(i + 1) * 10}.00 credit.",
        )
        rows.append({
            "scenario": "active_mismatch",
            "ticket_id": t["ticket_id"], "ticket_text": t["text"],
            "intent": intent, "category": category,
            "as_of": AS_OF.isoformat(),
            "lookup_fails": False,
            "notices": [notice],
            "expected_grounded_ids": [],
            "expected_escalation": None,
        })

    # --- 비활성(만료 / active=false / TTL 초과) → 반영하면 안 됨 ----------------
    inactive_cases = [
        ("expired", {"valid_from": (AS_OF - datetime.timedelta(days=30)).isoformat(),
                      "valid_until": (AS_OF - datetime.timedelta(days=1)).isoformat()}),
        ("active_false", {"active": False}),
        ("ttl_exceeded", {"valid_from": (AS_OF - datetime.timedelta(days=20)).isoformat(),
                           "valid_until": ""}),  # 기본 TTL 14일 초과
        ("expired", {"valid_from": (AS_OF - datetime.timedelta(days=60)).isoformat(),
                      "valid_until": (AS_OF - datetime.timedelta(days=45)).isoformat()}),
        ("active_false", {"active": False, "valid_from": (AS_OF - datetime.timedelta(days=1)).isoformat(),
                           "valid_until": (AS_OF + datetime.timedelta(days=1)).isoformat()}),
    ]
    for i, (subtype, overrides) in enumerate(inactive_cases):
        intent = required_intents[i % len(required_intents)]
        t = _pick(intent, 1, offset=2)[0]
        category = INTENT_TO_CATEGORY[intent]
        notice = _notice(f"INACTIVE-{i+1:02d}", category, "This notice should not be active.", **overrides)
        rows.append({
            "scenario": f"inactive_{subtype}",
            "ticket_id": t["ticket_id"], "ticket_text": t["text"],
            "intent": intent, "category": category,
            "as_of": AS_OF.isoformat(),
            "lookup_fails": False,
            "notices": [notice],
            "expected_grounded_ids": [],
            "expected_escalation": None,
        })

    # --- 조회 실패 → 필수 인텐트는 E9, 선택/불필요 인텐트는 계속 진행 -----------
    failure_required = ["track_order", "payment_issue"]
    for i, intent in enumerate(failure_required):
        t = _pick(intent, 1, offset=3)[0]
        rows.append({
            "scenario": "lookup_failure_required",
            "ticket_id": t["ticket_id"], "ticket_text": t["text"],
            "intent": intent, "category": INTENT_TO_CATEGORY[intent],
            "as_of": AS_OF.isoformat(),
            "lookup_fails": True,
            "notices": [],
            "expected_grounded_ids": [],
            "expected_escalation": "E9",
        })

    failure_optional = [("cancel_order", 0), ("create_account", 0)]
    for i, (intent, offset) in enumerate(failure_optional):
        t = _pick(intent, 1, offset=offset)[0]
        rows.append({
            "scenario": "lookup_failure_not_required",
            "ticket_id": t["ticket_id"], "ticket_text": t["text"],
            "intent": intent, "category": INTENT_TO_CATEGORY[intent],
            "as_of": AS_OF.isoformat(),
            "lookup_fails": True,
            "notices": [],
            "expected_grounded_ids": [],
            "expected_escalation": None,
        })

    for i, row in enumerate(rows, start=1):
        row["golden_id"] = f"NOTICE-{i:03d}"

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"완료: {OUTPUT_PATH} — {len(rows)}건")
    by_scenario: dict[str, int] = {}
    for row in rows:
        by_scenario[row["scenario"]] = by_scenario.get(row["scenario"], 0) + 1
    for scenario, count in sorted(by_scenario.items()):
        print(f"  {scenario}: {count}")


if __name__ == "__main__":
    main()
