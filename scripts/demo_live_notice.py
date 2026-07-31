#!/usr/bin/env python3
"""라이브 공지 실물 before/after 데모 (Phase 12c 최종 완료 기준 a·b).

같은 배송 문의 티켓을 **공지 소스를 끈 상태 / 실제 노션에 붙인 상태**로 각각
돌려 초안이 실제로 달라지는지 본다. 12a에서 stub으로 확인한 것을 실물로 재확인하는
단계다 — 노션에 쓰지 않고(읽기 전용 계약) 소스를 켰다/껐다 하는 방식으로 대조한다.

**URL을 외울 필요가 없게 하는 것이 이 스크립트의 목적이다.** `.env`에는 compose
형태(`http://notion-mcp:3000/mcp`)만 두면 되고, 이 스크립트는 앱을 호스트에서
돌리므로 컨테이너를 임시 호스트 포트로 띄운 뒤 그 주소로 자동 덮어쓴다.

사용:
    .venv/bin/python scripts/demo_live_notice.py

전제(.env): NOTION_TOKEN · NOTION_MCP_TOKEN · NOTICE_DB_ID.
LLM을 실제로 호출하므로 API 비용이 발생한다(프로덕션 백엔드 2회 실행).
"""
import argparse
import asyncio
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

CONTAINER = "notion-mcp-demo"
HOST_PORT = 3399  # 데모 전용 임시 포트(compose의 내부 포트와 무관)

TICKET = {
    "ticket_id": "DEMO-NOTICE-01",
    "text": "Hi, I ordered last week — when will my package actually arrive?",
    "customer_id": "",
    "order_id": "",
}
TRIAGE = {
    "intent": "delivery_period",
    "category": "DELIVERY",
    "confidence": 0.95,
    "requires_human": False,
}

# scope 대조용 — **본문도 그 인텐트의 문의여야 한다.** 인텐트 라벨만 바꾸고 배송
# 문의 본문을 그대로 두면 모델이 본문을 따라 배송 답변을 쓰는 게 당연해서, 공지
# 반영이 scope 때문인지 본문 때문인지 구분되지 않는다(첫 시도에서 실제로 그랬다).
#
# payment_issue를 쓰는 이유: **NOTICE_REQUIRED에 있어 에이전트가 공지 도구를
# 실제로 호출**하지만 카테고리는 PAYMENT라 DELIVERY 공지와 scope가 어긋난다.
# 즉 "공지를 눈으로 보고도 반영하지 않는가"를 시험한다 — 도구를 아예 호출하지
# 않는 인텐트(create_account 등)로는 이 FP를 측정할 수 없다.
PAYMENT_TICKET = {
    "ticket_id": "DEMO-NOTICE-SCOPE",
    "text": "My card was declined twice when I tried to pay. What payment methods can I use?",
    "customer_id": "",
    "order_id": "",
}


def _start_container() -> None:
    token = os.environ["NOTION_TOKEN"]
    auth = os.environ["NOTION_MCP_TOKEN"]
    headers = json.dumps(
        {"Authorization": f"Bearer {token}", "Notion-Version": "2025-09-03"}
    )
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(
        [
            "docker", "run", "-d", "--name", CONTAINER,
            "-p", f"127.0.0.1:{HOST_PORT}:3000",
            "-e", "OPENAPI_MCP_HEADERS", "-e", "AUTH_TOKEN",
            "mcp/notion",
            "--transport", "http", "--host", "0.0.0.0", "--port", "3000",
        ],
        env={**os.environ, "OPENAPI_MCP_HEADERS": headers, "AUTH_TOKEN": auth},
        check=True, capture_output=True,
    )
    # 앱을 호스트에서 돌리므로 compose 서비스명 대신 로컬 포트로 덮어쓴다.
    os.environ["NOTION_MCP_URL"] = f"http://127.0.0.1:{HOST_PORT}/mcp"


async def _run(label: str, notice_source: str, intent: str, category: str,
               base_ticket: dict | None = None) -> dict:
    os.environ["NOTICE_SOURCE"] = notice_source

    from app.common.mcp.notices.backends import notion as notion_backend
    from app.modules.reply.graph import run_reply

    notion_backend.clear_caches()

    base = base_ticket or TICKET
    ticket = {**base, "ticket_id": f"{base['ticket_id']}-{label}"}
    triage = {**TRIAGE, "intent": intent, "category": category}
    state = await run_reply(ticket, triage)

    print(f"\n{'=' * 70}\n{label}  (NOTICE_SOURCE={notice_source}, intent={intent})\n{'=' * 70}")
    print(f"outcome={state['outcome']} escalation={state.get('escalation_reason') or '-'}")
    print(f"tools_used={state['draft']['tools_used']}")
    print(f"cited={state['draft']['cited_policies']}")
    print(f"\n--- 초안 ---\n{state['draft']['reply_text'] or '(초안 없음)'}")
    return state


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="끝나고 컨테이너를 남긴다")
    parser.add_argument("--scope-only", action="store_true",
                        help="before/after를 건너뛰고 scope 대조만 (LLM 비용 절약)")
    args = parser.parse_args()

    for key in ("NOTION_TOKEN", "NOTION_MCP_TOKEN", "NOTICE_DB_ID"):
        if not os.getenv(key):
            sys.exit(f"[중단] .env에 {key}가 없습니다.")

    os.environ.setdefault("LLM_BACKEND", "anthropic")
    os.environ.setdefault("JUDGE_BACKEND", "openai")

    _start_container()
    try:
        await asyncio.sleep(2)  # 컨테이너 기동 대기

        before = after = None
        if not args.scope_only:
            before = await _run("BEFORE (공지 기능 끔)", "noop", "delivery_period", "DELIVERY")
            after = await _run("AFTER (실제 노션 공지)", "notion", "delivery_period", "DELIVERY")

        # (b) scope 불일치 — PAYMENT 문의에 DELIVERY 공지가 반영되면 안 된다
        scope_test = await _run(
            "SCOPE 불일치 (PAYMENT 티켓)", "notion", "payment_issue", "PAYMENT",
            base_ticket=PAYMENT_TICKET,
        )

        print(f"\n{'=' * 70}\n요약\n{'=' * 70}")
        if before and after:
            changed = before["draft"]["reply_text"] != after["draft"]["reply_text"]
            print(f"초안이 달라졌는가: {changed}")
            print(f"AFTER가 공지 도구를 호출했는가: "
                  f"{'check_live_notices' in after['draft']['tools_used']}")

        scope_draft = scope_test["draft"]["reply_text"].lower()
        called = "check_live_notices" in scope_test["draft"]["tools_used"]
        leaked = any(w in scope_draft for w in ("delay", "carrier", "behind schedule"))
        print(f"scope 불일치 티켓 outcome: {scope_test['outcome']}")
        print(f"  공지 도구를 호출했는가(호출해야 FP 측정이 성립): {called}")
        print(f"  DELIVERY 공지를 반영했는가(=FP, False여야 정상): {leaked}")
    finally:
        if not args.keep:
            subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
            print("\n(데모 컨테이너 정리 완료)")


if __name__ == "__main__":
    asyncio.run(main())
