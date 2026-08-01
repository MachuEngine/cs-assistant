#!/usr/bin/env python3
"""notices_golden.jsonl 기반 라이브 공지 평가 — Phase 12, DESIGN.md 6.2절.

측정 3종(PROMPTS.md Phase 12a 8절):
  - 도구 호출 여부(소스 선택 정확도)
  - 인지 대상 공지 FP/FN — grounded_notices가 골든의 expected_grounded_ids와 일치하는가
  - 게이트⑥ 발동 건수 — grounded 공지를 applied_notices에 넣지 않으면 실제로 거부되는가
  + E9 판정 정확도(조회 실패 → 필수 인텐트만 에스컬레이션)

## 날짜를 평행이동하는 이유 (핵심)

골든셋은 재현성을 위해 각 행에 고정 기준일(`as_of`)을 박아두는데, 실제 파이프라인
(`check_live_notices` → `is_notice_active`)은 **실행 시점의 UTC 오늘**을 쓴다. 이 간극을
그냥 두면 둘 중 하나를 포기해야 한다 — 순수 함수만 채점하면(재현 가능하지만 파이프라인을
안 거침) CLAUDE.md가 경계하는 "고정 출력 채점"에 가까워지고, 파이프라인을 그대로 돌리면
날짜가 바뀔 때마다 결과가 달라진다(실제로 NOTICE-011에서 어긋났다).

**해법**: 골든 행의 모든 날짜를 `(오늘 - as_of)`만큼 평행이동해서 stub에 주입한다.
공지 간 상대 관계(활성/만료/TTL 초과)가 전부 보존되므로 판정 결과는 그대로이고,
파이프라인은 실제 코드 경로를 그대로 탄다. **프로덕션 코드에 날짜 조작 손잡이를
넣지 않아도 된다** — 운영에서 실수로 켜지면 만료 공지가 되살아나는 위험을 피한다.

evals/golden/·evals/runners/는 보호 경로다. 이 러너는 골든셋을 읽기만 한다.

[2026-08-01 수정] run_triage.py에서 실측된 것과 같은 결함 — check_live_notices
자체는 내부에서 조회 실패를 잡아 fail-fast 계약을 지키지만(app/modules/reply/tools.py),
_run_row()가 부르는 save_draft나 세션 초기화 쪽에서 예기치 못한 예외(예: 골든 행
필드 누락, KNOWN_CLAUSE_IDS 참조 오류)가 나면 러너 전체가 죽는다. 다른 러너와
동일한 원칙으로 한 건의 실패를 "미달성"으로 기록하고 계속 진행하도록 고친다.
"""
import asyncio
import datetime
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

os.environ.setdefault("NOTICE_SOURCE", "stub")

from _common import load_jsonl, parse_args, write_report  # noqa: E402

from app.common.mcp.notices.backends import stub as notice_stub  # noqa: E402
from app.modules.reply import tools as reply_tools  # noqa: E402
from app.modules.reply.routing import requires_live_notices  # noqa: E402

GOLDEN_PATH = "evals/golden/notices_golden.jsonl"

_DISCLAIMER = (
    "This is a draft prepared by an AI assistant. A human agent is "
    "responsible for reviewing and approving it before it is sent."
)


def _shift(date_str: str, delta: datetime.timedelta) -> str:
    """"YYYY-MM-DD"를 delta만큼 이동. 빈 문자열(기본 TTL 사용)은 그대로 둔다."""
    if not date_str:
        return ""
    return (datetime.date.fromisoformat(date_str) + delta).isoformat()


def _shift_notices(row: dict) -> list[dict]:
    """골든 행의 공지 날짜를 오늘 기준으로 평행이동한다(위 docstring 참고)."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    delta = today - datetime.date.fromisoformat(row["as_of"])
    return [
        {
            **n,
            "valid_from": _shift(n.get("valid_from", ""), delta),
            "valid_until": _shift(n.get("valid_until", ""), delta),
        }
        for n in row["notices"]
    ]


async def _run_row(row: dict) -> dict:
    notice_stub.reset()
    if row["lookup_fails"]:
        notice_stub.set_failure(RuntimeError("golden-injected lookup failure"))
    else:
        notice_stub.set_notices(_shift_notices(row))

    reply_tools.bind_session()
    reply_tools.init_session(
        ticket_text=row["ticket_text"], order_id="", intent=row["intent"]
    )

    # 실제 도구를 그대로 호출한다 — 활성 판정·scope 대조·근거 승격 전부 프로덕션 경로
    await reply_tools.check_live_notices.ainvoke({})
    ctx = reply_tools.get_ctx()

    expected = sorted(row["expected_grounded_ids"])
    got = sorted(n["notice_id"] for n in ctx["grounded_notices"])

    # E9: 조회 실패 + 공지 필수 인텐트
    expected_e9 = row["expected_escalation"] == "E9"
    got_e9 = requires_live_notices(row["intent"]) and ctx["notice_lookup_failed"]

    # 게이트⑥: grounded 공지가 있는데 applied_notices에 안 넣으면 거부돼야 한다.
    # 게이트④(정책 인용)가 먼저 걸리는 인텐트가 있어 인용을 미리 넣어 격리한다.
    gate6_rejected = None
    if got:
        from app.modules.reply.tools import KNOWN_CLAUSE_IDS

        citation = sorted(KNOWN_CLAUSE_IDS)[0]
        reply_text = (
            f"Thank you for reaching out. Per [{citation}] we have reviewed your "
            f"request and confirmed the details below. {_DISCLAIMER}"
        )
        result = reply_tools.save_draft.invoke({"reply_text": reply_text})
        gate6_rejected = result.startswith("Rejected") and "applied_notices" in result

    return {
        "golden_id": row["golden_id"],
        "scenario": row["scenario"],
        "intent": row["intent"],
        "tool_called": ctx["notices_checked"],
        "expected_grounded": expected,
        "got_grounded": got,
        "grounded_fp": sorted(set(got) - set(expected)),
        "grounded_fn": sorted(set(expected) - set(got)),
        "grounded_match": got == expected,
        "gate6_rejected": gate6_rejected,
        "expected_e9": expected_e9,
        "got_e9": got_e9,
        "e9_match": got_e9 == expected_e9,
        "row_error": None,
    }


def _failed_row_result(row: dict, error: str) -> dict:
    """run_triage.py와 동일한 원칙 — 실패를 조용히 빼지 않고 '미달성'으로 기록한다."""
    expected = sorted(row["expected_grounded_ids"])
    expected_e9 = row["expected_escalation"] == "E9"
    return {
        "golden_id": row["golden_id"],
        "scenario": row["scenario"],
        "intent": row["intent"],
        "tool_called": False,
        "expected_grounded": expected,
        "got_grounded": [],
        "grounded_fp": [],
        "grounded_fn": expected,
        "grounded_match": False,
        "gate6_rejected": None,
        "expected_e9": expected_e9,
        "got_e9": False,
        "e9_match": expected_e9 is False,
        "row_error": error,
    }


async def main() -> None:
    args = parse_args(default_sample=20)
    golden = load_jsonl(GOLDEN_PATH)
    rows = golden if (args.full or args.all) else golden[: args.sample]

    results = []
    row_failures = []
    for row in rows:
        try:
            result = await _run_row(row)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            row_failures.append({"golden_id": row["golden_id"], "scenario": row["scenario"], "error": error})
            result = _failed_row_result(row, error)
        results.append(result)
        print(
            f"{result['golden_id']} {result['scenario']:<28} "
            f"tool={result['tool_called']} "
            f"grounded={result['got_grounded']} (기대 {result['expected_grounded']}) "
            f"gate6={result['gate6_rejected']} e9={result['got_e9']}"
            + (f" ERROR={result['row_error']}" if result["row_error"] else "")
        )

    n = len(results)
    fp_rows = [r for r in results if r["grounded_fp"]]
    fn_rows = [r for r in results if r["grounded_fn"]]
    gate6_rows = [r for r in results if r["gate6_rejected"] is not None]

    report = {
        "n": n,
        "tool_call_rate": sum(r["tool_called"] for r in results) / n if n else None,
        "grounded_accuracy": sum(r["grounded_match"] for r in results) / n if n else None,
        "grounded_fp_rate": len(fp_rows) / n if n else None,
        "grounded_fn_rate": len(fn_rows) / n if n else None,
        "gate6_applicable": len(gate6_rows),
        "gate6_triggered": sum(1 for r in gate6_rows if r["gate6_rejected"]),
        "e9_accuracy": sum(r["e9_match"] for r in results) / n if n else None,
        "results": results,
        "row_failures": row_failures,
        "row_failure_count": len(row_failures),
    }
    path = write_report("run_notices", report)

    print(
        f"\ntool_call_rate={report['tool_call_rate']} "
        f"grounded_accuracy={report['grounded_accuracy']} "
        f"(FP {report['grounded_fp_rate']} / FN {report['grounded_fn_rate']})"
    )
    print(
        f"게이트⑥ {report['gate6_triggered']}/{report['gate6_applicable']}건 발동 · "
        f"e9_accuracy={report['e9_accuracy']}"
    )
    if row_failures:
        print(f"행 처리 실패(미달성으로 기록): {len(row_failures)}건")
    print(f"리포트 저장: {path}")


if __name__ == "__main__":
    asyncio.run(main())
