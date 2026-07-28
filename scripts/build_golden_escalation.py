#!/usr/bin/env python3
"""escalation_golden.jsonl 생성 — DESIGN.md 6.3절.

E1~E4/E6과 대조군(정상 처리)은 100% 결정론적으로 구성 가능하다:
- E1~E4는 app.modules.reply.routing.check_pre_agent_escalation()이 순수하게
  (intent, confidence, flags)만 보고 판정하므로, 실제 티켓 텍스트에 원하는
  intent/confidence/flags 조합만 골든 row에 실어두면 정답이 확정된다.
- E6은 하이드레이션 단계(hydrate_tickets.py)가 이미 "존재하지 않는 주문번호"를
  order_exists=false로 심어뒀으므로, 그 사실을 그대로 재사용한다.

E5(에이전트 자체 판단)/E7(save_draft 3연속 실패)/E8(예산 소진)은 Phase 6
reply agent의 실시간 판단이 있어야만 확정되는, 모델 행동에 달린 결과라
100% 결정론적으로 만들 수 없다 — best_effort=true로 표시하고, 노리는
목표(target_reason)만 남긴다. run_escalation.py와 EVAL.md는 이 10건을
정답 일치가 아니라 "에스컬레이션이 일어났는가"(recall)로만 채점한다.

evals/golden/은 보호 경로라 이 스크립트의 출력은 Bash 실행으로만 만든다.
"""
import json
import pathlib
import random

TICKETS_PATH = pathlib.Path("data/synthetic/tickets.jsonl")
OUTPUT_PATH = pathlib.Path("evals/golden/escalation_golden.jsonl")
SEED = 42
N_PER_DETERMINISTIC_BUCKET = 5


def _load_tickets() -> list[dict]:
    with open(TICKETS_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def main() -> None:
    if OUTPUT_PATH.exists():
        print(f"이미 존재함, 건너뜀: {OUTPUT_PATH}")
        return

    tickets = _load_tickets()
    rng = random.Random(SEED)
    rng.shuffle(tickets)

    rows = []

    # E1 — confidence < TRIAGE_CONFIDENCE_THRESHOLD(기본 0.70). 실제 인텐트는
    # 무관하고(어느 인텐트든 confidence가 낮으면 E1), confidence만 golden row가
    # 직접 명시한다 — 실제 분류기를 다시 돌리지 않아도 정답이 고정된다.
    ordinary = [t for t in tickets if t["intent"] not in ("contact_human_agent", "complaint") and "W" not in t["flags"]]
    for t in ordinary[:N_PER_DETERMINISTIC_BUCKET]:
        rows.append({
            "scenario": "E1", "deterministic": True,
            "ticket_id": t["ticket_id"], "ticket_text": t["text"],
            "triage": {"intent": t["intent"], "confidence": 0.5, "flags": t["flags"]},
            "order_id": t["order_id"], "order_exists": t["order_exists"],
            "expected_should_escalate": True, "expected_reason": "E1",
        })

    # E2 — intent == contact_human_agent, confidence은 threshold 위로 둬서
    # E1이 아니라 E2가 발동하도록 격리한다.
    e2_pool = [t for t in tickets if t["intent"] == "contact_human_agent"]
    for t in e2_pool[:N_PER_DETERMINISTIC_BUCKET]:
        rows.append({
            "scenario": "E2", "deterministic": True,
            "ticket_id": t["ticket_id"], "ticket_text": t["text"],
            "triage": {"intent": t["intent"], "confidence": 0.9, "flags": t["flags"]},
            "order_id": t["order_id"], "order_exists": t["order_exists"],
            "expected_should_escalate": True, "expected_reason": "E2",
        })

    # E3 — intent == complaint, 마찬가지로 confidence를 threshold 위로.
    e3_pool = [t for t in tickets if t["intent"] == "complaint"]
    for t in e3_pool[:N_PER_DETERMINISTIC_BUCKET]:
        rows.append({
            "scenario": "E3", "deterministic": True,
            "ticket_id": t["ticket_id"], "ticket_text": t["text"],
            "triage": {"intent": t["intent"], "confidence": 0.9, "flags": t["flags"]},
            "order_id": t["order_id"], "order_exists": t["order_exists"],
            "expected_should_escalate": True, "expected_reason": "E3",
        })

    # E4 — flags에 실제로 "W"(공격적 언어)가 있는 실제 티켓, confidence는 위로.
    e4_pool = [t for t in tickets if "W" in t["flags"] and t["intent"] not in ("contact_human_agent", "complaint")]
    for t in e4_pool[:N_PER_DETERMINISTIC_BUCKET]:
        rows.append({
            "scenario": "E4", "deterministic": True,
            "ticket_id": t["ticket_id"], "ticket_text": t["text"],
            "triage": {"intent": t["intent"], "confidence": 0.9, "flags": t["flags"]},
            "order_id": t["order_id"], "order_exists": t["order_exists"],
            "expected_should_escalate": True, "expected_reason": "E4",
        })

    # E6 — order_exists == False (hydrate_tickets.py가 의도적으로 심은 가짜
    # 주문번호). pre-agent 조건은 전부 통과(높은 confidence, 일반 intent,
    # 공격적 표현 없음)해서 진짜 E6 경로만 격리한다.
    e6_pool = [
        t for t in tickets
        if t["order_exists"] is False and t["intent"] not in ("contact_human_agent", "complaint") and "W" not in t["flags"]
    ]
    for t in e6_pool[:N_PER_DETERMINISTIC_BUCKET]:
        rows.append({
            "scenario": "E6", "deterministic": True,
            "ticket_id": t["ticket_id"], "ticket_text": t["text"],
            "triage": {"intent": t["intent"], "confidence": 0.9, "flags": t["flags"]},
            "order_id": t["order_id"], "order_exists": t["order_exists"],
            "expected_should_escalate": True, "expected_reason": "E6",
        })

    # 대조군 — 에스컬레이션이 전혀 필요 없는 정상 케이스. order_exists가 True인
    # order-linked 인텐트를 우선으로 섞어서 lookup_order도 정상 통과하게 한다.
    control_pool = [
        t for t in tickets
        if t["intent"] not in ("contact_human_agent", "complaint")
        and "W" not in t["flags"]
        and t["order_exists"] is not False
    ]
    for t in control_pool[:N_PER_DETERMINISTIC_BUCKET]:
        rows.append({
            "scenario": "none", "deterministic": True,
            "ticket_id": t["ticket_id"], "ticket_text": t["text"],
            "triage": {"intent": t["intent"], "confidence": 0.9, "flags": t["flags"]},
            "order_id": t["order_id"], "order_exists": t["order_exists"],
            "expected_should_escalate": False, "expected_reason": None,
        })

    # E5/E7/E8 — best-effort. 모델의 실시간 판단에 달려 있어 100% 보장 못 함.
    # run_escalation.py/EVAL.md는 이 구간을 "에스컬레이션 발생 여부"로만 채점한다.
    best_effort = [
        {
            "scenario": "E5", "target_reason": "E5",
            "ticket_id": "GOLDEN-E5-01",
            "ticket_text": "Can you unsubscribe my old phone number from marketing texts sent through a defunct third-party carrier that no longer exists?",
            "triage": {"intent": "newsletter_subscription", "confidence": 0.9, "flags": ""},
            "order_id": "", "order_exists": None,
        },
        {
            "scenario": "E5", "target_reason": "E5",
            "ticket_id": "GOLDEN-E5-02",
            "ticket_text": "I want to permanently merge two customer accounts into one and keep both order histories intact.",
            "triage": {"intent": "switch_account", "confidence": 0.9, "flags": ""},
            "order_id": "", "order_exists": None,
        },
        {
            "scenario": "E5", "target_reason": "E5",
            "ticket_id": "GOLDEN-E5-03",
            "ticket_text": "Can you retroactively apply VIP tier pricing to all my orders from the last two years?",
            "triage": {"intent": "check_refund_policy", "confidence": 0.9, "flags": ""},
            "order_id": "", "order_exists": None,
        },
        {
            "scenario": "E5", "target_reason": "E5",
            "ticket_id": "GOLDEN-E5-04",
            "ticket_text": "Can you set up a recurring subscription that automatically places a new order every week without me confirming each time?",
            "triage": {"intent": "newsletter_subscription", "confidence": 0.9, "flags": ""},
            "order_id": "", "order_exists": None,
        },
        {
            "scenario": "E7", "target_reason": "E7",
            "ticket_id": "GOLDEN-E7-01",
            "ticket_text": "My order ORD-000001 was both partially delivered and fully refunded at the same time — which policy applies and what do I owe or get back?",
            "triage": {"intent": "get_refund", "confidence": 0.9, "flags": ""},
            "order_id": "ORD-000001", "order_exists": True,
        },
        {
            "scenario": "E7", "target_reason": "E7",
            "ticket_id": "GOLDEN-E7-02",
            "ticket_text": "I was charged twice for ORD-000002, refunded once, then re-charged a cancellation fee — please reconcile all three charges.",
            "triage": {"intent": "payment_issue", "confidence": 0.9, "flags": ""},
            "order_id": "ORD-000002", "order_exists": True,
        },
        {
            "scenario": "E7", "target_reason": "E7",
            "ticket_id": "GOLDEN-E7-03",
            "ticket_text": "My invoice for ORD-000005 shows a different total than what I was charged, and a third amount was mentioned on the phone — which one is correct and why?",
            "triage": {"intent": "check_invoice", "confidence": 0.9, "flags": ""},
            "order_id": "ORD-000005", "order_exists": True,
        },
        {
            "scenario": "E8", "target_reason": "E8",
            "ticket_id": "GOLDEN-E8-01",
            "ticket_text": "Please explain, citing every applicable clause, why my cancellation fee, refund amount, warranty coverage, and shipping restriction all conflict with each other for order ORD-000003.",
            "triage": {"intent": "check_cancellation_fee", "confidence": 0.9, "flags": ""},
            "order_id": "ORD-000003", "order_exists": True,
        },
        {
            "scenario": "E8", "target_reason": "E8",
            "ticket_id": "GOLDEN-E8-02",
            "ticket_text": "I need a single reply that satisfies both the Standard tier and VIP tier refund rules simultaneously for ORD-000004, whichever is more generous, cited exactly.",
            "triage": {"intent": "get_refund", "confidence": 0.9, "flags": ""},
            "order_id": "ORD-000004", "order_exists": True,
        },
        {
            "scenario": "E8", "target_reason": "E8",
            "ticket_id": "GOLDEN-E8-03",
            "ticket_text": "Please give me a definitive yes-or-no on whether ORD-000006 qualifies for free expedited shipping, full refund, warranty replacement, and a cancellation-fee waiver all at once, with a citation for each.",
            "triage": {"intent": "delivery_options", "confidence": 0.9, "flags": ""},
            "order_id": "ORD-000006", "order_exists": True,
        },
    ]
    for row in best_effort:
        row["deterministic"] = False
        row["expected_should_escalate"] = True
        row["expected_reason"] = None  # 정확한 사유코드는 참고값 — target_reason만 노림
        rows.append(row)

    for i, row in enumerate(rows, start=1):
        row["golden_id"] = f"ESC-{i:03d}"

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    det = sum(1 for r in rows if r["deterministic"])
    print(f"완료: {OUTPUT_PATH} — {len(rows)}건 (결정론적 {det}건, best-effort {len(rows) - det}건)")


if __name__ == "__main__":
    main()
