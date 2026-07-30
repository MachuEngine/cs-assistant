import pytest

from app.modules.reply.routing import (
    ALL_CATEGORIES,
    ALL_INTENTS,
    ESCALATION_REASONS,
    NOTICE_REQUIRED,
    check_pre_agent_escalation,
    requires_check_customer_tier,
    requires_live_notices,
    requires_lookup_order,
    requires_policy_citation,
    requires_search_policy,
)


def test_all_27_intents_present():
    assert len(ALL_INTENTS) == 27


def test_all_11_categories_present():
    assert len(ALL_CATEGORIES) == 11
    assert ALL_CATEGORIES == {
        "ACCOUNT", "CANCEL", "CONTACT", "DELIVERY", "FEEDBACK", "INVOICE",
        "ORDER", "PAYMENT", "REFUND", "SHIPPING", "SUBSCRIPTION",
    }


@pytest.mark.parametrize(
    "intent,expected",
    [
        ("cancel_order", True),
        ("change_order", True),
        ("track_order", True),
        ("check_cancellation_fee", True),
        ("get_refund", True),
        ("track_refund", True),
        ("change_shipping_address", True),
        ("payment_issue", True),
        ("check_invoice", True),
        ("get_invoice", True),
        ("place_order", False),
        ("delivery_options", False),
        ("create_account", False),
    ],
)
def test_requires_lookup_order(intent, expected):
    assert requires_lookup_order(intent) is expected


@pytest.mark.parametrize(
    "intent,expected",
    [
        ("cancel_order", True),
        ("check_refund_policy", True),
        ("delivery_options", True),
        ("place_order", False),
        ("track_order", False),
        ("create_account", False),
    ],
)
def test_requires_search_policy(intent, expected):
    assert requires_search_policy(intent) is expected


@pytest.mark.parametrize(
    "intent,expected",
    [
        ("check_cancellation_fee", True),
        ("get_refund", True),
        ("delivery_options", True),
        ("cancel_order", False),
        ("place_order", False),
    ],
)
def test_requires_check_customer_tier(intent, expected):
    assert requires_check_customer_tier(intent) is expected


def test_no_citation_intents_match_no_search_policy():
    # ACCOUNT/SUBSCRIPTION/FEEDBACK/CONTACT 계열은 정책 인용이 필요 없다
    no_citation_intents = {
        "create_account", "delete_account", "edit_account", "switch_account",
        "recover_password", "registration_problems",
        "newsletter_subscription", "review", "complaint",
        "contact_human_agent", "contact_customer_service",
        "place_order", "set_up_shipping_address",
        "track_order", "payment_issue", "check_invoice", "get_invoice",
        "track_refund",
    }
    for intent in no_citation_intents:
        assert requires_policy_citation(intent) is False, intent


def test_citation_required_matches_search_policy_required():
    for intent in ALL_INTENTS:
        assert requires_policy_citation(intent) == requires_search_policy(intent)


# --- E1~E4 에스컬레이션 조건 ------------------------------------------------

def test_e1_low_confidence_triggers_escalation(monkeypatch):
    monkeypatch.setenv("TRIAGE_CONFIDENCE_THRESHOLD", "0.70")
    assert check_pre_agent_escalation("cancel_order", 0.5, "B") == "E1"


def test_no_escalation_when_confidence_sufficient(monkeypatch):
    monkeypatch.setenv("TRIAGE_CONFIDENCE_THRESHOLD", "0.70")
    assert check_pre_agent_escalation("cancel_order", 0.9, "B") is None


def test_e2_human_agent_request_triggers_escalation(monkeypatch):
    monkeypatch.setenv("TRIAGE_CONFIDENCE_THRESHOLD", "0.70")
    assert check_pre_agent_escalation("contact_human_agent", 0.95, "B") == "E2"


def test_e3_complaint_triggers_escalation(monkeypatch):
    monkeypatch.setenv("TRIAGE_CONFIDENCE_THRESHOLD", "0.70")
    assert check_pre_agent_escalation("complaint", 0.95, "B") == "E3"


def test_e4_offensive_flag_triggers_escalation(monkeypatch):
    monkeypatch.setenv("TRIAGE_CONFIDENCE_THRESHOLD", "0.70")
    assert check_pre_agent_escalation("cancel_order", 0.95, "BQW") == "E4"


def test_e1_takes_priority_over_other_conditions(monkeypatch):
    # confidence 미달이면 다른 조건과 무관하게 E1이 먼저 잡힌다
    monkeypatch.setenv("TRIAGE_CONFIDENCE_THRESHOLD", "0.70")
    assert check_pre_agent_escalation("complaint", 0.3, "W") == "E1"


def test_default_threshold_is_070(monkeypatch):
    monkeypatch.delenv("TRIAGE_CONFIDENCE_THRESHOLD", raising=False)
    assert check_pre_agent_escalation("cancel_order", 0.69, "B") == "E1"
    assert check_pre_agent_escalation("cancel_order", 0.70, "B") is None


# --- 라이브 공지 조회(Phase 12a) ---------------------------------------------

def test_notice_required_has_exactly_seven_intents():
    # PROMPTS.md 원문의 6개에 check_payment_methods를 사람이 필수로 추가 확정
    # (2026-07-30) — 이 테스트가 그 집합의 정확한 구성을 회귀로부터 지킨다.
    assert NOTICE_REQUIRED == {
        "delivery_period", "delivery_options", "track_order", "track_refund",
        "payment_issue", "change_shipping_address", "check_payment_methods",
    }


@pytest.mark.parametrize(
    "intent,expected",
    [
        ("delivery_period", True),
        ("delivery_options", True),
        ("track_order", True),
        ("track_refund", True),
        ("payment_issue", True),
        ("change_shipping_address", True),
        ("check_payment_methods", True),
        ("cancel_order", False),
        ("create_account", False),
        ("place_order", False),
    ],
)
def test_requires_live_notices(intent, expected):
    assert requires_live_notices(intent) is expected


def test_e9_label_present_and_english():
    assert "E9" in ESCALATION_REASONS
    assert ESCALATION_REASONS["E9"].isascii()
