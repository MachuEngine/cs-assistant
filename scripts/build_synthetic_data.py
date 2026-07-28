#!/usr/bin/env python3
"""합성 정책 문서(Northwind Retail) + 주문/고객 SQLite 생성.

DESIGN.md 4.4절. 시드 고정으로 재현 가능하다. 실제 고객 데이터는 쓰지 않는다 —
customers/orders 는 전부 랜덤 생성된 가상 레코드다.
"""
import datetime
import pathlib
import random
import sqlite3

SEED = 42
NUM_CUSTOMERS = 800
NUM_ORDERS = 3000

POLICIES_DIR = pathlib.Path("data/synthetic/policies")
DB_PATH = pathlib.Path("data/synthetic/shop.db")

FIRST_NAMES = [
    "Olivia", "Liam", "Emma", "Noah", "Ava", "Ethan", "Sophia", "Mason",
    "Isabella", "Lucas", "Mia", "Henry", "Amelia", "Jack", "Harper", "Owen",
    "Evelyn", "Leo", "Charlotte", "Wyatt", "Grace", "Julian", "Chloe", "Levi",
    "Zoey", "Aiden", "Layla", "Gabriel", "Nora", "Carter",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson",
    "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
    "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez",
    "Lewis", "Robinson", "Walker",
]
COUNTRIES = [
    "United States", "Canada", "United Kingdom", "Germany", "France",
    "Australia", "Japan", "Brazil", "India", "South Africa", "Mexico",
    "Spain", "Italy", "Netherlands", "Sweden",
]
CARRIERS = ["Northwind Express", "GlobalPost", "QuickShip Courier", "MerchantLine Freight"]
CURRENCIES = ["USD", "EUR", "GBP"]
TIERS = ["standard", "plus", "vip"]
TIER_WEIGHTS = [0.70, 0.22, 0.08]
ORDER_STATUSES = ["pending", "processing", "shipped", "delivered", "cancelled", "returned"]
STATUS_WEIGHTS = [0.05, 0.10, 0.15, 0.55, 0.05, 0.10]


def random_date(rng: random.Random, start: datetime.date, end: datetime.date) -> datetime.date:
    span = (end - start).days
    return start + datetime.timedelta(days=rng.randint(0, span))


def build_customers(rng: random.Random) -> list[tuple]:
    rows = []
    for i in range(1, NUM_CUSTOMERS + 1):
        customer_id = f"CUST-{i:06d}"
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        tier = rng.choices(TIERS, weights=TIER_WEIGHTS, k=1)[0]
        joined_at = random_date(rng, datetime.date(2022, 1, 1), datetime.date(2026, 6, 1))
        country = rng.choice(COUNTRIES)
        rows.append((customer_id, name, tier, joined_at.isoformat(), country))
    return rows


def build_orders(rng: random.Random, customer_ids: list[str]) -> list[tuple]:
    rows = []
    for i in range(1, NUM_ORDERS + 1):
        order_id = f"ORD-{i:06d}"
        customer_id = rng.choice(customer_ids)
        status = rng.choices(ORDER_STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
        carrier = rng.choice(CARRIERS)
        tracking_no = f"TRK{rng.randint(10**9, 10**10 - 1)}"
        ordered_at = random_date(rng, datetime.date(2025, 1, 1), datetime.date(2026, 7, 20))
        delivered_at = None
        if status in ("delivered", "returned"):
            delivered_at = ordered_at + datetime.timedelta(days=rng.randint(3, 14))
        amount = round(rng.uniform(15, 350), 2)
        currency = rng.choice(CURRENCIES)
        rows.append((
            order_id, customer_id, status, carrier, tracking_no,
            ordered_at.isoformat(),
            delivered_at.isoformat() if delivered_at else None,
            amount, currency,
        ))
    return rows


def build_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        print(f"이미 존재함: {DB_PATH} (재생성 생략)")
        return

    rng = random.Random(SEED)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE customers (
            customer_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            tier TEXT NOT NULL,
            joined_at TEXT NOT NULL,
            country TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE orders (
            order_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            status TEXT NOT NULL,
            carrier TEXT NOT NULL,
            tracking_no TEXT NOT NULL,
            ordered_at TEXT NOT NULL,
            delivered_at TEXT,
            amount REAL NOT NULL,
            currency TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        )
    """)

    customers = build_customers(rng)
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", customers)

    customer_ids = [c[0] for c in customers]
    orders = build_orders(rng, customer_ids)
    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", orders)

    conn.commit()
    conn.close()
    print(f"완료: {DB_PATH} — customers {len(customers)}건, orders {len(orders)}건")


POLICY_DOCS: dict[str, str] = {
    "membership_tiers.md": """# Northwind Retail — Membership Tiers

## TIER-01: Tier Overview
Northwind Retail customers are automatically assigned one of three membership
tiers based on account history: Standard, Plus, or VIP. Tier determines return
windows, refund processing speed, shipping costs, warranty coverage, and
cancellation fee waivers as described in the policy documents referenced below.

## TIER-02: Standard Tier
Default tier for all new accounts. No special benefits beyond the baseline
service levels described in each policy document.

## TIER-03: Plus Tier
Awarded to customers with an active Northwind Plus subscription. Benefits:
extended return window, free return shipping, expedited refund processing,
extended warranty coverage.

## TIER-04: VIP Tier
Reserved for customers with cumulative lifetime spend above $2,000 or by
invitation. Benefits: longest return window, free return shipping, fastest
refund processing, cancellation fees waived, longest warranty coverage.
""",
    "returns_exchanges.md": """# Northwind Retail — Returns & Exchanges Policy

## RET-01: General Eligibility
Items may be returned or exchanged if they are unused, undamaged, and in their
original packaging with all tags attached. Proof of purchase (order number) is
required for all returns.

## RET-02: Return Window by Tier
The return window is measured from the delivery date recorded on the order.
- Standard tier: 14 days from delivery
- Plus tier: 30 days from delivery
- VIP tier: 60 days from delivery

Returns requested after the applicable window has closed are not eligible
under this policy and must be escalated to a supervisor for case-by-case
review.

## RET-03: Non-Returnable Items
The following items cannot be returned or exchanged under any tier: perishable
goods, gift cards, personalized or custom-made items, and opened personal care
or hygiene products.

## RET-04: Exchange Process
Exchanges are processed as a return of the original item followed by a new
order for the replacement item. Plus and VIP customers are not charged
additional shipping for the replacement order.

## RET-05: Return Shipping Cost
- Standard tier: the customer is responsible for return shipping cost.
- Plus and VIP tiers: a prepaid return shipping label is provided at no cost.

## RET-06: Condition of Returned Items
Items returned in a used, damaged, or incomplete condition are subject to a
partial refund as described in REF-03, at the discretion of the reviewing
agent.
""",
    "refunds.md": """# Northwind Retail — Refunds Policy

## REF-01: Refund Method
Refunds are issued only to the original payment method used at the time of
purchase. Refunds to a different card, account, or payment method are not
permitted.

## REF-02: Refund Processing Time by Tier
Processing time is measured from the date the returned item is received at the
Northwind Retail warehouse, or from the cancellation date for orders canceled
before shipment.
- Standard tier: 5-7 business days
- Plus tier: 3-4 business days
- VIP tier: 1-2 business days

## REF-03: Partial Refunds
If a returned item is used, damaged, or missing original packaging (see
RET-06), a restocking fee of up to 20% of the item price may be deducted from
the refund amount.

## REF-04: Refunds for Orders Canceled Before Shipment
Orders canceled before the "shipped" status (see CANC-01) are refunded in
full, with no restocking fee or cancellation fee.

## REF-05: Refund Confirmation
Customers receive an email confirmation once a refund has been processed. If
a refund does not appear within 3 additional business days after the
confirmation email, the customer should be directed to contact their card
issuer or bank.
""",
    "shipping.md": """# Northwind Retail — Shipping Policy

## SHIP-01: Standard Delivery Estimates
Standard shipping delivery estimates depend on destination:
- Domestic: 3-5 business days
- International: 7-14 business days

Estimates begin from the date the order status changes to "shipped," not the
order date.

## SHIP-02: Expedited Shipping by Tier
- Standard tier: expedited shipping is available for an additional fee at
  checkout.
- Plus tier: one free expedited shipping upgrade per calendar month.
- VIP tier: all orders ship with expedited service at no additional cost.

## SHIP-03: Shipping Address Changes
A shipping address may be changed only while the order status is "pending" or
"processing." Once an order status changes to "shipped," the address cannot be
changed; the customer should be advised to contact the shipping carrier
directly or arrange to intercept/return the package upon delivery.

## SHIP-04: Delivery Delays
If a package has not arrived within 3 business days after the delivery
estimate window in SHIP-01, the customer support agent should first check the
tracking status with the carrier before offering a reshipment or refund.

## SHIP-05: International Shipping Restrictions
Northwind Retail does not ship internationally to countries on Northwind's
restricted-destination list. Agents should check the current list in the
internal operations portal before confirming international delivery
availability.
""",
    "cancellation_fees.md": """# Northwind Retail — Order Cancellation Fee Policy

## CANC-01: No Fee — Order Not Yet Processing
Orders with status "pending" may be canceled at any time with no fee.

## CANC-02: Reduced Fee — Order Processing
Orders with status "processing" (payment captured, warehouse picking has not
yet started) incur a cancellation fee of 5% of the order total, except for VIP
tier customers, for whom this fee is waived.

## CANC-03: Cannot Cancel — Order Shipped
Orders with status "shipped" or "delivered" cannot be canceled. The customer
should be directed to the return process (see RET-01 through RET-06) instead.

## CANC-04: Order Modifications
Requests to modify an order (e.g., change quantity, size, or item) follow the
same eligibility window as cancellations in CANC-01 and CANC-02.
Modifications to an order that has already shipped are not possible; the
customer should be advised to complete the return/exchange process instead.
""",
    "warranty.md": """# Northwind Retail — Warranty Policy

## WARR-01: Standard Warranty Coverage
All physical products sold by Northwind Retail carry a manufacturer defect
warranty of 12 months from the delivery date, covering defects in materials or
workmanship under normal use. This warranty does not cover damage from misuse,
accidents, or normal wear and tear.

## WARR-02: Extended Warranty by Tier
- Plus tier: an additional 6 months of coverage beyond WARR-01 (18 months
  total).
- VIP tier: an additional 12 months of coverage beyond WARR-01 (24 months
  total).

## WARR-03: Warranty Claim Process
To file a warranty claim, the customer must provide the order number and a
description of the defect. Approved claims result in a free repair,
replacement, or store credit at Northwind Retail's discretion.
""",
    "payment_methods.md": """# Northwind Retail — Payment Methods Policy

## PAY-01: Accepted Payment Methods
Northwind Retail accepts major credit and debit cards, PayPal, and Northwind
Retail store credit / gift cards. Bank transfers and cash on delivery are not
supported.

## PAY-02: Payment Issue Troubleshooting
If a payment is declined at checkout, the customer should first verify the
billing address matches the card issuer's records and confirm the card has
not expired. If the issue persists after a retry, the agent should advise the
customer to contact their card issuer, as Northwind Retail does not have
visibility into the decline reason.

## PAY-03: Store Credit and Gift Cards
Store credit and gift card balances do not expire and may be checked via the
customer's account page. Store credit issued as a refund (see REF-01) can be
combined with one other payment method in a single order.
""",
}


def build_policies() -> None:
    POLICIES_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for filename, content in POLICY_DOCS.items():
        path = POLICIES_DIR / filename
        if path.exists():
            continue
        path.write_text(content, encoding="utf-8")
        written += 1
    print(f"완료: {POLICIES_DIR} — {len(POLICY_DOCS)}개 문서 중 {written}개 신규 작성")


if __name__ == "__main__":
    build_policies()
    build_db()
