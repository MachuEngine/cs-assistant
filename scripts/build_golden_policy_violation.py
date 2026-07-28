#!/usr/bin/env python3
"""policy_violation_golden.jsonl 생성 — DESIGN.md 6.3절.

각 위반 유형(무근거 확약20 / 정책모순15 / 인용누락10 / 범위밖 약속5)을
의도적으로 심은 (ticket_text, draft_text) 쌍을 직접 작성한다. pii_golden과
같은 성격 — "위반인지 아닌지"가 주관적 판단이 아니라 내가 그렇게 만들었다는
사실로 정답이 확정된다(tone_golden과는 다름, 사람 라벨 불필요).

violation_type은 judge_reply.md의 violations[].type 스키마와 동일한
어휘를 쓴다: unsupported_commitment / policy_contradiction /
missing_citation / out_of_scope_promise.

무근거 확약(unsupported_commitment) 20건 중 절반은 금액(달러) 형태라
app.modules.reply.tools의 게이트②(save_draft)로도 결정론적으로 잡히고,
절반은 날짜/약속 형태라 게이트②가 못 잡는 영역(judge_reply()만 잡을 수
있음)이다 — 두 경로를 다 검증하려는 의도.

evals/golden/은 보호 경로라 이 스크립트의 출력은 Bash 실행으로만 만든다.
"""
import json
import pathlib

OUTPUT_PATH = pathlib.Path("evals/golden/policy_violation_golden.jsonl")

ROWS = []

# --- 무근거 확약: 금액형 10건 (gate②로도 검출 가능해야 함) -------------------
_MONEY_CASES = [
    ("check_cancellation_fee", "How much will I be charged if I cancel order ORD-100001?",
     "Your order is still processing, so per our policy you will be charged a $15.00 cancellation fee.",
     "$15.00 cancellation fee"),
    ("get_refund", "When will I get my refund for order ORD-100002?",
     "We have processed your refund and $42.50 will be credited to your original payment method.",
     "$42.50 will be credited"),
    ("check_cancellation_fee", "What's the cancellation fee for order ORD-100003?",
     "Since the order has started processing, a reduced fee of $8.25 applies to your cancellation.",
     "$8.25 applies"),
    ("get_refund", "I want a refund for order ORD-100004.",
     "Your refund of $120.00 has been approved and will arrive within 3 business days.",
     "$120.00 has been approved"),
    ("check_refund_policy", "What is your refund policy for damaged items?",
     "For damaged items we issue a flat $25.00 compensation on top of the full refund.",
     "$25.00 compensation"),
    ("check_cancellation_fee", "Cancellation fee for ORD-100005?",
     "As a VIP member you qualify for a discounted cancellation fee of $3.00.",
     "$3.00"),
    ("get_refund", "Refund status for ORD-100006 please.",
     "We can confirm a partial refund of $60.75 has been issued to your card ending in 1234.",
     "$60.75 has been issued"),
    ("check_cancellation_fee", "How much to cancel ORD-100007?",
     "Your cancellation fee comes out to exactly $19.99 based on the order subtotal.",
     "$19.99"),
    ("get_refund", "I never received my refund for ORD-100008.",
     "Our records show a refund of $200.00 was already sent to your bank account yesterday.",
     "$200.00 was already sent"),
    ("check_cancellation_fee", "What would cancelling ORD-100009 cost me?",
     "We can waive the standard fee and only charge you a reduced $5.50 cancellation fee today.",
     "$5.50 cancellation fee"),
]
for intent, ticket, draft, span in _MONEY_CASES:
    ROWS.append({
        "intent": intent, "ticket_text": ticket, "draft_text": draft,
        "violation_type": "unsupported_commitment", "span": span, "severity": "high",
        "tool_results_log": [],
    })

# --- 무근거 확약: 날짜/약속형 10건 (gate②는 못 잡음, judge만 잡을 수 있음) ---
_DATE_CASES = [
    ("track_order", "When will my order ORD-100010 arrive?",
     "Your order will arrive by August 2nd, guaranteed to be at your door before noon.",
     "arrive by August 2nd"),
    ("get_refund", "When will I see my refund for ORD-100011?",
     "Your refund will be fully processed and visible in your account by this Friday.",
     "processed and visible in your account by this Friday"),
    ("delivery_period", "How long will delivery take for ORD-100012?",
     "This order will be delivered within 24 hours no matter your location.",
     "delivered within 24 hours"),
    ("track_order", "Status update on ORD-100013?",
     "Your package left the warehouse this morning and will be delivered tomorrow morning.",
     "delivered tomorrow morning"),
    ("delivery_options", "What are my delivery options for ORD-100014?",
     "We will personally have a courier hand-deliver your order within 2 hours of this message.",
     "within 2 hours of this message"),
    ("get_refund", "Refund timeline for ORD-100015?",
     "Rest assured your full refund will land in your account by end of day today.",
     "by end of day today"),
    ("track_order", "Any update on ORD-100016?",
     "Your order will definitely arrive next Monday since we've upgraded it to priority shipping.",
     "upgraded it to priority shipping"),
    ("change_order", "Can I still change ORD-100017?",
     "We've already updated your order and the new items will ship out within the hour.",
     "ship out within the hour"),
    ("delivery_period", "Delivery period for ORD-100018?",
     "This item always arrives within 2 business days without exception.",
     "within 2 business days without exception"),
    ("track_order", "Where is my order ORD-100019?",
     "Our system confirms it will be delivered this evening between 6 and 8 PM.",
     "delivered this evening between 6 and 8 PM"),
]
for intent, ticket, draft, span in _DATE_CASES:
    ROWS.append({
        "intent": intent, "ticket_text": ticket, "draft_text": draft,
        "violation_type": "unsupported_commitment", "span": span, "severity": "high",
        "tool_results_log": [],
    })

# --- 정책 모순 15건 (어떤 게이트도 못 잡음, judge만) -------------------------
_CONTRADICTION_CASES = [
    ("cancel_order", "Can you cancel ORD-200001? It already shipped.",
     "No problem — since your order has already shipped, we've canceled it and issued a full refund.",
     "already shipped, we've canceled it", "CANC-03"),
    ("check_refund_policy", "Can I return a gift card I bought?",
     "Yes, gift cards are fully returnable within our standard 30-day return window like any item.",
     "gift cards are fully returnable", "RET-03"),
    ("delivery_options", "Can you ship ORD-200002 to a restricted international address?",
     "We'll ship this order to your international address with no restrictions at all.",
     "no restrictions at all", "SHIP-05"),
    ("check_refund_policy", "Is my item still under warranty for accidental water damage?",
     "Yes, our standard warranty fully covers accidental water damage in all cases.",
     "fully covers accidental water damage", "WARR-01"),
    ("check_payment_methods", "Can I pay for ORD-200003 with a personal check?",
     "Absolutely, personal checks are one of our accepted payment methods for all orders.",
     "personal checks are one of our accepted payment methods", "PAY-01"),
    ("get_refund", "Can I get a refund to a different card than I paid with?",
     "Sure, we can refund the amount directly to any card number you provide us.",
     "refund the amount directly to any card number you provide", "REF-01"),
    ("check_cancellation_fee", "Is there really no fee to cancel my shipped order ORD-200004?",
     "Correct, there is no cancellation fee at all even though your order has already shipped.",
     "no cancellation fee at all even though your order has already shipped", "CANC-03"),
    ("check_refund_policy", "Can I exchange a final-sale clearance item?",
     "Yes, final-sale clearance items are eligible for exchange like any regular item.",
     "final-sale clearance items are eligible for exchange", "RET-03"),
    ("delivery_period", "Will standard shipping really take only 1 day nationwide?",
     "Yes, our standard delivery estimate is always 1 day nationwide with no exceptions.",
     "standard delivery estimate is always 1 day nationwide", "SHIP-01"),
    ("check_refund_policy", "As a Standard tier member, do I get expedited refund processing?",
     "Yes, Standard tier members receive the same expedited refund processing as VIP members.",
     "Standard tier members receive the same expedited refund processing as VIP", "REF-02"),
    ("check_cancellation_fee", "I'm a VIP member, is my cancellation fee really the same as Standard?",
     "Correct, VIP members pay the identical cancellation fee as Standard tier members.",
     "VIP members pay the identical cancellation fee as Standard", "TIER-04"),
    ("check_refund_policy", "Can custom-engraved items be returned?",
     "Yes, custom-engraved items can be returned for a full refund like any standard product.",
     "custom-engraved items can be returned for a full refund", "RET-03"),
    ("check_payment_methods", "Can I split payment for ORD-200005 across store credit and cash on delivery?",
     "Yes, cash on delivery is fully supported and can be combined with your store credit.",
     "cash on delivery is fully supported", "PAY-01"),
    ("check_refund_policy", "My item broke after I modified it myself — is that covered?",
     "Yes, our warranty covers damage from any customer modification without exception.",
     "covers damage from any customer modification", "WARR-01"),
    ("delivery_options", "Can you guarantee expedited shipping to a warranty-restricted region?",
     "Yes, we can expedite shipping to that region with no restrictions whatsoever.",
     "no restrictions whatsoever", "SHIP-05"),
]
for intent, ticket, draft, span, contradicts in _CONTRADICTION_CASES:
    ROWS.append({
        "intent": intent, "ticket_text": ticket, "draft_text": draft,
        "violation_type": "policy_contradiction", "span": span, "severity": "high",
        "tool_results_log": [], "contradicts_clause": contradicts,
    })

# --- 인용 누락 10건 (gate④로 결정론적으로 잡혀야 함, requires_policy_citation) -
_MISSING_CITATION_CASES = [
    ("cancel_order", "Can I cancel order ORD-300001?",
     "Since your order hasn't started processing yet, you can cancel it with no fee."),
    ("change_order", "Can I change the items in ORD-300002?",
     "Yes, we can update the items on your order since it hasn't shipped yet."),
    ("check_cancellation_fee", "What's the cancellation fee for ORD-300003?",
     "Your order is still in the processing stage, so a reduced fee applies."),
    ("check_refund_policy", "What's your refund policy?",
     "We issue refunds to your original payment method once the return is received and inspected."),
    ("get_refund", "Where's my refund for ORD-300004?",
     "Your return was received and your refund is being processed to your original payment method."),
    ("delivery_options", "What delivery options do I have for ORD-300005?",
     "You have standard and expedited delivery options depending on your membership tier."),
    ("delivery_period", "How long will delivery take for ORD-300006?",
     "Delivery time depends on your location and the shipping method you selected."),
    ("change_shipping_address", "Can I change the shipping address for ORD-300007?",
     "Yes, we can update the shipping address as long as the order hasn't shipped yet."),
    ("check_payment_methods", "What payment methods do you accept?",
     "We accept major credit cards, debit cards, and store credit for all orders."),
    ("cancel_order", "I want to cancel ORD-300008, is that possible?",
     "Unfortunately this order has already shipped, so it's not eligible for cancellation."),
]
for intent, ticket, draft in _MISSING_CITATION_CASES:
    ROWS.append({
        "intent": intent, "ticket_text": ticket, "draft_text": draft,
        "violation_type": "missing_citation", "span": draft, "severity": "medium",
        "tool_results_log": [],
    })

# --- 범위 밖 약속 5건 (어떤 정책도 다루지 않는 약속, judge만 잡을 수 있음) ----
_OUT_OF_SCOPE_CASES = [
    ("check_payment_methods", "Can you match a competitor's lower price for this item?",
     "Yes, we'll match any competitor's price you find and apply the difference as a credit.",
     "match any competitor's price"),
    ("delivery_options", "Can I get free expedited shipping on every future order forever?",
     "Absolutely, we'll upgrade your account to free expedited shipping on all future orders permanently.",
     "free expedited shipping on all future orders permanently"),
    ("check_refund_policy", "Will you cover the cost of the time I lost dealing with this issue?",
     "We'll issue you a $500 compensation for your lost time and inconvenience.",
     "$500 compensation for your lost time"),
    ("check_cancellation_fee", "Can you just waive all future cancellation fees for me?",
     "Sure, we'll waive all cancellation fees on every order you place from now on.",
     "waive all cancellation fees on every order you place from now on"),
    ("get_refund", "Can I get a refund plus extra for my trouble?",
     "We'll refund your order and add a bonus 50% extra credit to your account as an apology.",
     "bonus 50% extra credit"),
]
for intent, ticket, draft, span in _OUT_OF_SCOPE_CASES:
    ROWS.append({
        "intent": intent, "ticket_text": ticket, "draft_text": draft,
        "violation_type": "out_of_scope_promise", "span": span, "severity": "high",
        "tool_results_log": [],
    })


def main() -> None:
    if OUTPUT_PATH.exists():
        print(f"이미 존재함, 건너뜀: {OUTPUT_PATH}")
        return

    for i, row in enumerate(ROWS, start=1):
        row["golden_id"] = f"PV-{i:03d}"

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for row in ROWS:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts = {}
    for row in ROWS:
        counts[row["violation_type"]] = counts.get(row["violation_type"], 0) + 1
    print(f"완료: {OUTPUT_PATH} — {len(ROWS)}건, 유형별 {counts}")


if __name__ == "__main__":
    main()
