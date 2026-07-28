# data/

## raw/ — Bitext Customer Support Dataset

- **출처**: [Bitext Customer Support LLM Chatbot Training Dataset](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset) (Hugging Face)
- **라이선스**: **CDLA-Sharing-1.0** (share-alike) — 재배포 시 동일 라이선스·출처 표기 필요
- **재현**: `python scripts/download_bitext.py` (커밋되지 않음, `.gitignore` 대상)
- **실측 규모**: 26,872행, 27개 인텐트, 11개 카테고리, 영어 전용 (DESIGN.md 4.1절)
- **⚠️ `response` 컬럼은 정답셋·RAG 코퍼스로 쓰지 않는다** — 우리 정책 문서에 근거하지 않는 범용 템플릿이다(DESIGN.md 4.2절)

## synthetic/ — 합성 데이터 (직접 생성, 실제 고객 데이터 아님)

| 경로 | 생성 스크립트 | 내용 |
|---|---|---|
| `policies/*.md` | `scripts/build_synthetic_data.py` | 가상 이커머스사 Northwind Retail 영문 정책 7종. 조항 번호 부여(`RET-03` 등) |
| `shop.db` | `scripts/build_synthetic_data.py` | SQLite. `customers(customer_id, name, tier, joined_at, country)`, `orders(order_id, customer_id, status, carrier, tracking_no, ordered_at, delivered_at, amount, currency)` |
| `tickets.jsonl` | `scripts/hydrate_tickets.py` | Bitext instruction의 엔티티 플레이스홀더를 `shop.db` 실제 값으로 치환한 결과. `{ticket_id, text, intent, category, flags, customer_id, order_id, order_exists}` |

`order_exists`는 `order_id`가 실제 `shop.db`에 존재하는지(`true`) 아니면 의도적으로 존재하지 않게 채운 것인지(`false`, 약 10%)를 기록한다 — 에스컬레이션 조건 E6(주문 조회 실패) 골든셋 정답 산출에 쓴다.

재현 순서:
```bash
python scripts/download_bitext.py       # data/raw/
python scripts/build_synthetic_data.py  # data/synthetic/policies/, shop.db
python scripts/hydrate_tickets.py       # data/synthetic/tickets.jsonl (shop.db 필요)
```

**⛔ 절대 금지**: 실제 고객 문의, 실제 주문 정보, 식별 가능한 개인정보를 이 디렉토리에 추가하지 않는다.
