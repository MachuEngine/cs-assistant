from app.common.privacy import mask_pii


def test_masks_email():
    assert mask_pii("contact me at jane.doe@example.com please") == (
        "contact me at {{EMAIL}} please"
    )


def test_masks_phone_number_variants():
    assert "{{PHONE}}" in mask_pii("call me at 555-123-4567")
    assert "{{PHONE}}" in mask_pii("my number is (555) 123-4567")
    assert "{{PHONE}}" in mask_pii("reach me at +1 555 123 4567")


def test_masks_luhn_valid_card_number():
    # 4111111111111111 — 결제 테스트용으로 널리 쓰이는 Luhn-valid Visa 번호
    assert mask_pii("my card is 4111111111111111") == "my card is {{CARD}}"


def test_does_not_mask_luhn_invalid_digit_sequence():
    # 마지막 자리를 바꿔 Luhn 검증에 실패하게 만든 시퀀스 — 카드로 오판하면 안 된다
    text = "reference number 4111111111111112"
    assert "{{CARD}}" not in mask_pii(text)


def test_masks_address():
    result = mask_pii("please ship it to 456 Oak Avenue")
    assert "{{ADDRESS}}" in result
    assert "Oak Avenue" not in result


def test_masks_known_person_name():
    result = mask_pii("this is John Smith, I need help with my order")
    assert "{{NAME}}" in result
    assert "John Smith" not in result


def test_does_not_mask_order_number():
    assert mask_pii("question about cancelling order ORD-002830") == (
        "question about cancelling order ORD-002830"
    )


def test_does_not_mask_invoice_number():
    assert mask_pii("show me invoice INV-000045") == "show me invoice INV-000045"


def test_does_not_mask_customer_id():
    assert mask_pii("customer CUST-000123 needs help") == (
        "customer CUST-000123 needs help"
    )


def test_does_not_mask_unrelated_capitalized_phrase():
    # 회사명·지명 등 일반 Title Case 두 단어는 가제티어에 없으면 마스킹하지 않는다
    result = mask_pii("I ordered from Northwind Retail last week")
    assert "{{NAME}}" not in result


def test_combined_pii_all_masked():
    text = (
        "Hi, this is John Smith. My email is jane.doe@example.com, "
        "phone 555-123-4567, card 4111111111111111, "
        "ship to 456 Oak Avenue, order ORD-002830."
    )
    result = mask_pii(text)
    for token in ("{{NAME}}", "{{EMAIL}}", "{{PHONE}}", "{{CARD}}", "{{ADDRESS}}"):
        assert token in result
    assert "ORD-002830" in result
