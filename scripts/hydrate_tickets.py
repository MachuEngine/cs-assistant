#!/usr/bin/env python3
"""Bitext instruction의 엔티티 플레이스홀더를 shop.db 실제 값으로 치환 → tickets.jsonl.

DESIGN.md 4.1·4.3절. {{Order Number}} 등은 문자열 리터럴이지 실제 값이 아니므로
그대로 두면 lookup_order 가 조회할 대상이 없다. order-linked 인텐트(라우팅 표
3.2절에서 lookup_order 가 "필수"인 10종)에는 order_id 를 배정하며, 그중 10%는
의도적으로 존재하지 않는 주문번호로 채워 에스컬레이션 E6 경로를 재현한다.
"""
import csv
import json
import pathlib
import random
import re
import sqlite3

SEED = 42
FAKE_ORDER_RATE = 0.10

RAW_CSV = pathlib.Path("data/raw/bitext_customer_support.csv")
DB_PATH = pathlib.Path("data/synthetic/shop.db")
OUTPUT_PATH = pathlib.Path("data/synthetic/tickets.jsonl")

# DESIGN.md 3.2 라우팅 표에서 lookup_order 가 "필수"인 인텐트.
# app/modules/reply/routing.py(Phase 6) 작성 시 반드시 이 목록과 동일해야 한다 —
# 갈라지면 하이드레이션 시점의 order_id 배정과 실제 런타임 도구 요구가 어긋난다.
ORDER_LINKED_INTENTS = {
    "cancel_order", "change_order", "track_order",
    "check_cancellation_fee",
    "get_refund", "track_refund",
    "change_shipping_address",
    "payment_issue",
    "check_invoice", "get_invoice",
}

# DB와 무관한 플레이스홀더용 고정 어휘(DESIGN.md 4.1절 실측 근거)
ACCOUNT_TYPE_POOL = ["personal", "business", "student", "family"]
ACCOUNT_CATEGORY_POOL = ["premium", "basic", "corporate", "student"]
CITY_POOL = [
    "Toronto", "Berlin", "Sydney", "Tokyo", "São Paulo", "Mumbai",
    "Cape Town", "Dubai", "Mexico City", "London", "Singapore", "Seoul",
]
COUNTRY_POOL = [
    "Canada", "Germany", "Australia", "Japan", "Brazil", "India",
    "South Africa", "United Arab Emirates", "Mexico", "United Kingdom",
    "Singapore", "South Korea",
]
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}

PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")


def load_bitext_rows() -> list[dict]:
    with open(RAW_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_shop_data():
    conn = sqlite3.connect(DB_PATH)
    customers = {
        row[0]: {"name": row[1], "tier": row[2]}
        for row in conn.execute("SELECT customer_id, name, tier FROM customers")
    }
    orders_by_customer: dict[str, list[dict]] = {}
    for order_id, customer_id, amount, currency in conn.execute(
        "SELECT order_id, customer_id, amount, currency FROM orders"
    ):
        orders_by_customer.setdefault(customer_id, []).append(
            {"order_id": order_id, "amount": amount, "currency": currency}
        )
    conn.close()
    return customers, orders_by_customer, list(customers.keys()), list(orders_by_customer.keys())


def fake_order_id(rng: random.Random) -> str:
    # 실제 주문은 ORD-000001~ORD-003000 범위뿐이라 이 구간 밖 번호는 항상 존재하지 않는다.
    return f"ORD-{rng.randint(800000, 899999):06d}"


def hydrate_text(text: str, values: dict) -> str:
    def replace(match: re.Match) -> str:
        return values.get(match.group(1), match.group(0))
    return PLACEHOLDER_RE.sub(replace, text)


def main() -> None:
    rng = random.Random(SEED)
    rows = load_bitext_rows()
    customers, orders_by_customer, all_customer_ids, customers_with_orders = load_shop_data()

    tickets = []
    for i, row in enumerate(rows, start=1):
        intent = row["intent"]
        customer_id = rng.choice(all_customer_ids)

        order_id = ""
        order_exists = None
        linked_order = None

        if intent in ORDER_LINKED_INTENTS:
            if rng.random() < FAKE_ORDER_RATE:
                order_id = fake_order_id(rng)
                order_exists = False
            else:
                # 배정된 고객이 주문이 없으면(약 2.5%), 주문이 있는 고객으로 바꿔
                # customer_id/order_id 가 항상 서로 일치하게 한다.
                if customer_id not in orders_by_customer:
                    customer_id = rng.choice(customers_with_orders)
                linked_order = rng.choice(orders_by_customer[customer_id])
                order_id = linked_order["order_id"]
                order_exists = True

        customer = customers[customer_id]

        if linked_order is not None:
            refund_amount = f"{linked_order['amount']:.2f}"
            currency_symbol = CURRENCY_SYMBOLS.get(linked_order["currency"], "$")
        else:
            refund_amount = f"{rng.uniform(15, 350):.2f}"
            currency_symbol = rng.choice(list(CURRENCY_SYMBOLS.values()))

        invoice_number = ("INV-" + order_id.removeprefix("ORD-")) if order_id else ""

        values = {
            "Order Number": order_id,
            "Invoice Number": invoice_number,
            "Refund Amount": refund_amount,
            "Currency Symbol": currency_symbol,
            "Person Name": customer["name"],
            "Account Type": rng.choice(ACCOUNT_TYPE_POOL),
            "Account Category": rng.choice(ACCOUNT_CATEGORY_POOL),
            "Delivery City": rng.choice(CITY_POOL),
            "Delivery Country": rng.choice(COUNTRY_POOL),
        }

        tickets.append({
            "ticket_id": f"TCK-{i:06d}",
            "text": hydrate_text(row["instruction"], values),
            "intent": intent,
            "category": row["category"],
            "flags": row["flags"],
            "customer_id": customer_id,
            "order_id": order_id,
            "order_exists": order_exists,
        })

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for t in tickets:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    print(f"완료: {OUTPUT_PATH} — {len(tickets)}건")


if __name__ == "__main__":
    main()
