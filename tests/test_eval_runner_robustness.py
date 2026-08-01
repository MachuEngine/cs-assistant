"""evals/runners/의 러너들이 한 건 실패해도 전체 리포트를 살려서 끝내는지 검증.

run_triage.py에서 실측된 버그(--full 200건 중 1건이 fail-fast로 던지면 러너
전체가 죽고 97건치 API 비용이 리포트 없이 날아감, 2026-08-01 커밋 caca5f4)와
동일한 패턴이 run_escalation/run_judge_reliability/run_notices/
run_policy_violation에도 있었다(2026-08-01 야간 자율 검토에서 발견·evals/runners/
패치는 사람이 적용해야 함 — 보호 경로).

evals/runners/는 패키지가 아니라(각 스크립트가 스스로 sys.path를 조작) 파일
경로로 직접 로드한다. 골든 파일은 읽지 않고(러너 함수만 필요) 동작 검증에
필요한 최소 합성 데이터를 인메모리로 주입한다. LLM/네트워크를 부르는 함수는
전부 monkeypatch로 대체한다.

이 파일은 evals/runners/의 4개 패치본(run_escalation.py, run_judge_reliability.py,
run_notices.py, run_policy_violation.py)이 적용된 뒤에만 통과한다 — 패치 전에는
run_triage.py가 고쳐지기 전과 동일하게 예외가 러너 전체를 죽여 실패한다.
"""
import asyncio
import importlib.util
import pathlib
import sys

RUNNERS_DIR = pathlib.Path(__file__).resolve().parent.parent / "evals" / "runners"


def _load_runner(name: str):
    spec = importlib.util.spec_from_file_location(f"_test_runner_{name}", RUNNERS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _no_op_write_report(monkeypatch, module):
    captured = {}

    def _fake_write_report(name, data):
        captured["data"] = data
        return pathlib.Path(f"/tmp/{name}.json")

    monkeypatch.setattr(module, "write_report", _fake_write_report)
    return captured


def test_run_escalation_survives_one_pipeline_failure(monkeypatch):
    module = _load_runner("run_escalation")
    monkeypatch.setattr(sys, "argv", ["run_escalation.py"])

    golden_rows = [
        {"golden_id": "T-E6-1", "scenario": "E6", "ticket_id": "TCK-1", "ticket_text": "a",
         "order_id": "", "expected_should_escalate": True,
         "triage": {"intent": "track_order", "confidence": 0.9, "flags": ""}},
        {"golden_id": "T-E6-2", "scenario": "E6", "ticket_id": "TCK-2", "ticket_text": "b",
         "order_id": "", "expected_should_escalate": True,
         "triage": {"intent": "track_order", "confidence": 0.9, "flags": ""}},
    ]
    monkeypatch.setattr(module, "load_jsonl", lambda path: golden_rows)

    calls = {"n": 0}

    async def _fake_run_reply(ticket, triage):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated backend timeout")
        return {"outcome": "escalated", "escalation_reason": "E6"}

    monkeypatch.setattr(module, "run_reply", _fake_run_reply)
    captured = _no_op_write_report(monkeypatch, module)

    asyncio.run(module.main())  # 예외 없이 끝나야 한다

    report = captured["data"]
    assert report["n_slow"] == 2
    assert len(report["e6_results"]) == 2
    assert report["pipeline_failure_count"] == 1
    failed_entry = next(r for r in report["e6_results"] if r["golden_id"] == "T-E6-1")
    assert failed_entry["exact_match"] is False
    assert failed_entry["pipeline_error"] is not None
    ok_entry = next(r for r in report["e6_results"] if r["golden_id"] == "T-E6-2")
    assert ok_entry["exact_match"] is True


def test_run_judge_reliability_survives_one_judge_failure(monkeypatch):
    module = _load_runner("run_judge_reliability")
    monkeypatch.setattr(sys, "argv", ["run_judge_reliability.py"])

    golden_rows = [
        {"golden_id": "TONE-A", "ticket_text": "a", "draft_text": "d1", "human_tone_score": 4,
         "tool_results_log": []},
        {"golden_id": "TONE-B", "ticket_text": "b", "draft_text": "d2", "human_tone_score": 5,
         "tool_results_log": []},
    ]
    monkeypatch.setattr(module, "load_jsonl", lambda path: golden_rows)
    monkeypatch.setattr(module, "get_judge_backend", lambda: object())

    calls = {"n": 0}

    async def _fake_judge_reply(ticket_text, draft_text, violations, llm, tool_results_log=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated judge backend error")
        return {"tone": 5, "policy_compliance": 5, "violations": [], "reasoning": "ok"}

    monkeypatch.setattr(module, "judge_reply", _fake_judge_reply)
    captured = _no_op_write_report(monkeypatch, module)

    asyncio.run(module.main())

    report = captured["data"]
    assert report["n_used"] == 2
    assert report["judge_failure_count"] == 1
    assert len(report["rows"]) == 2


def test_run_policy_violation_survives_one_judge_failure(monkeypatch):
    module = _load_runner("run_policy_violation")
    monkeypatch.setattr(sys, "argv", ["run_policy_violation.py"])

    golden_rows = [
        {"golden_id": "PV-A", "ticket_text": "a", "draft_text": "d1",
         "violation_type": "policy_contradiction", "intent": "track_order", "tool_results_log": []},
        {"golden_id": "PV-B", "ticket_text": "b", "draft_text": "d2",
         "violation_type": "policy_contradiction", "intent": "track_order", "tool_results_log": []},
    ]
    monkeypatch.setattr(module, "load_jsonl", lambda path: golden_rows)
    monkeypatch.setattr(module, "get_judge_backend", lambda: object())

    calls = {"n": 0}

    async def _fake_judge_reply(ticket_text, draft_text, violations, llm, tool_results_log=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated judge backend error")
        return {"violations": [{"type": "policy_contradiction"}]}

    monkeypatch.setattr(module, "judge_reply", _fake_judge_reply)
    captured = _no_op_write_report(monkeypatch, module)

    asyncio.run(module.main())

    report = captured["data"]
    assert report["n"] == 2
    assert report["judge_failure_count"] == 1
    # 실패 건은 분모(per_type_total)에는 남되 분자(per_type_hit)에는 안 들어가 recall을 부풀리지 않는다
    assert report["judge_per_type_recall"]["policy_contradiction"] == 0.5


def test_run_notices_survives_one_row_failure(monkeypatch):
    module = _load_runner("run_notices")
    monkeypatch.setattr(sys, "argv", ["run_notices.py"])

    golden_rows = [
        {"golden_id": "N-A", "scenario": "s", "intent": "track_order", "as_of": "2026-08-01",
         "lookup_fails": False, "notices": [], "expected_grounded_ids": [], "expected_escalation": None,
         "ticket_text": "a"},
        {"golden_id": "N-B", "scenario": "s", "intent": "track_order", "as_of": "2026-08-01",
         "lookup_fails": False, "notices": [], "expected_grounded_ids": [], "expected_escalation": None,
         "ticket_text": "b"},
    ]
    monkeypatch.setattr(module, "load_jsonl", lambda path: golden_rows)

    orig_run_row = module._run_row
    calls = {"n": 0}

    async def _flaky_run_row(row):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated tool error")
        return await orig_run_row(row)

    monkeypatch.setattr(module, "_run_row", _flaky_run_row)
    captured = _no_op_write_report(monkeypatch, module)

    asyncio.run(module.main())

    report = captured["data"]
    assert report["n"] == 2
    assert report["row_failure_count"] == 1
