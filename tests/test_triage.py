"""Triage 모듈 테스트 (Phase 5 완료 기준).

마스킹-먼저 순서 테스트는 실제 LLM 호출 없이(모킹) 확인한다 — 이 테스트가
검증하는 것은 "classify_ticket에 도달하는 텍스트가 이미 마스킹됐는가"이지
모델 품질이 아니다. 실제 20건 분류는 로컬 Ollama로 pytest.mark.llm_live에서
확인한다(완료 기준의 "샘플 티켓 20건 분류").
"""
import json
import pathlib

import pytest

from app.modules.triage import classifier
from app.modules.triage.classifier import TriageResult, triage_ticket

TICKETS_PATH = pathlib.Path("data/synthetic/tickets.jsonl")


@pytest.mark.asyncio
async def test_masking_happens_before_model_call(monkeypatch):
    """classify_ticket이 받는 텍스트에 원본 PII가 없어야 한다 — 마스킹이
    모델 호출보다 먼저 실행됐다는 뜻이다(CLAUDE.md 하드룰)."""
    captured = {}

    async def fake_classify_ticket(masked_text: str) -> TriageResult:
        captured["text"] = masked_text
        return TriageResult(
            intent="cancel_order", category="ORDER", confidence=0.9, reason="test"
        )

    monkeypatch.setattr(classifier, "classify_ticket", fake_classify_ticket)

    raw_text = "Hi, this is John Smith, my email is jane.doe@example.com, cancel order ORD-000001"
    await triage_ticket(raw_text, flags="B")

    assert "text" in captured, "classify_ticket이 호출되지 않았다"
    assert "John Smith" not in captured["text"]
    assert "jane.doe@example.com" not in captured["text"]
    assert "{{NAME}}" in captured["text"]
    assert "{{EMAIL}}" in captured["text"]
    # 주문번호는 마스킹 대상이 아니다 — lookup_order가 써야 한다
    assert "ORD-000001" in captured["text"]


@pytest.mark.asyncio
async def test_requires_human_true_when_confidence_low(monkeypatch):
    async def fake_classify_ticket(masked_text: str) -> TriageResult:
        return TriageResult(
            intent="cancel_order", category="ORDER", confidence=0.4, reason="unclear"
        )

    monkeypatch.setattr(classifier, "classify_ticket", fake_classify_ticket)
    monkeypatch.setenv("TRIAGE_CONFIDENCE_THRESHOLD", "0.70")

    result = await triage_ticket("some ticket text")
    assert result["requires_human"] is True
    assert result["escalation_reason"] == "E1"


@pytest.mark.asyncio
async def test_requires_human_false_when_confidence_high(monkeypatch):
    async def fake_classify_ticket(masked_text: str) -> TriageResult:
        return TriageResult(
            intent="track_order", category="ORDER", confidence=0.95, reason="clear"
        )

    monkeypatch.setattr(classifier, "classify_ticket", fake_classify_ticket)
    monkeypatch.setenv("TRIAGE_CONFIDENCE_THRESHOLD", "0.70")

    result = await triage_ticket("where is my package")
    assert result["requires_human"] is False
    assert result["escalation_reason"] is None
    assert result["reason"] == "clear"


@pytest.mark.asyncio
async def test_classify_ticket_rejects_unknown_intent(monkeypatch):
    async def fake_ainvoke(self, messages):
        return TriageResult(
            intent="not_a_real_intent", category="ORDER", confidence=0.9, reason="x"
        )

    class FakeStructuredLLM:
        async def ainvoke(self, messages):
            return TriageResult(
                intent="not_a_real_intent", category="ORDER", confidence=0.9, reason="x"
            )

    class FakeLLM:
        def with_structured_output(self, schema):
            return FakeStructuredLLM()

    monkeypatch.setattr(classifier, "get_llm_backend", lambda: FakeLLM())

    with pytest.raises(ValueError, match="알 수 없는 인텐트"):
        await classifier.classify_ticket("already masked text")


@pytest.mark.llm_live
@pytest.mark.asyncio
async def test_classify_20_sample_tickets_via_ollama(monkeypatch):
    """로컬 Ollama로 실제 티켓 20건을 분류한다 — 완료 기준의 '샘플 티켓 20건'."""
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:14b")

    lines = TICKETS_PATH.read_text(encoding="utf-8").splitlines()
    sample = [json.loads(lines[i]) for i in range(0, len(lines), len(lines) // 20)][:20]

    correct = 0
    for ticket in sample:
        result = await triage_ticket(ticket["text"], flags=ticket["flags"])
        assert result["intent"] in classifier.ALL_INTENTS
        assert result["category"] in classifier.ALL_CATEGORIES
        assert 0.0 <= result["confidence"] <= 1.0
        if result["intent"] == ticket["intent"]:
            correct += 1

    print(f"\nOllama qwen2.5:14b 샘플 20건 인텐트 정확도(참고용): {correct}/20")
