#!/usr/bin/env python3
"""tone_golden.jsonl "후보" 생성 — DESIGN.md 6.3절.

톤 라벨(human_tone_score, 1~5점)은 사람이 직접 매겨야 한다 — 생성 모델도
개발 세션도 전부 같은 계열(Claude)이라 내가 라벨까지 채우면 Judge 신뢰도
검증(κ)이 "Judge vs 사람"이 아니라 "Judge vs 같은 계열 모델"이 되어버려
무의미해진다(사용자와 합의된 사항). 이 스크립트는 딱 절반만 한다 — 실제
Phase 6 파이프라인(run_reply())을 로컬 Ollama로 돌려 (ticket, draft) 후보
30건을 만드는 것까지. human_tone_score는 null로 남긴다.

다양한 인텐트/카테고리에서 티켓을 뽑되, E1~E4/E6처럼 애초에 초안 자체가
안 나오는 케이스(contact_human_agent, complaint, W 플래그, order_exists=False)는
후보 풀에서 제외한다 — 톤을 평가할 초안이 있어야 의미가 있다.

llm_live 성격의 스크립트라 오래 걸린다(건당 1~2분). Bash로 백그라운드
실행을 권장한다. evals/golden/은 보호 경로라 출력은 Bash 실행으로만 만든다.
"""
import asyncio
import json
import os
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

os.environ.setdefault("LLM_BACKEND", "ollama")
os.environ.setdefault("JUDGE_BACKEND", "ollama")
os.environ.setdefault("OLLAMA_MODEL", "qwen2.5:14b")

from app.common.privacy import mask_pii  # noqa: E402
from app.modules.reply.graph import run_reply  # noqa: E402

TICKETS_PATH = pathlib.Path("data/synthetic/tickets.jsonl")
OUTPUT_PATH = pathlib.Path("evals/golden/tone_golden.jsonl")
SEED = 42
TARGET_COUNT = 30
MAX_ATTEMPTS = 60  # auto_draft가 아닌 경우(escalated)를 대비한 여유분
EXCLUDED_INTENTS = {"contact_human_agent", "complaint"}


def _load_candidate_pool() -> list[dict]:
    tickets = []
    with open(TICKETS_PATH, encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            if t["intent"] in EXCLUDED_INTENTS:
                continue
            if "W" in t["flags"]:
                continue
            if t["order_exists"] is False:
                continue
            tickets.append(t)

    rng = random.Random(SEED)
    rng.shuffle(tickets)

    by_intent: dict[str, list[dict]] = {}
    for t in tickets:
        by_intent.setdefault(t["intent"], []).append(t)

    # 인텐트 다양성 확보 — 라운드로빈으로 뽑아 특정 인텐트에 쏠리지 않게 한다.
    ordered = []
    intents = sorted(by_intent)
    idx = 0
    while len(ordered) < MAX_ATTEMPTS and any(by_intent.values()):
        intent = intents[idx % len(intents)]
        if by_intent[intent]:
            ordered.append(by_intent[intent].pop())
        idx += 1
    return ordered[:MAX_ATTEMPTS]


async def main() -> None:
    if OUTPUT_PATH.exists():
        print(f"이미 존재함, 건너뜀: {OUTPUT_PATH}")
        return

    candidates = _load_candidate_pool()
    print(f"후보 티켓 {len(candidates)}건에서 auto_draft {TARGET_COUNT}건을 모을 때까지 시도합니다.")

    rows = []
    for i, raw in enumerate(candidates, start=1):
        if len(rows) >= TARGET_COUNT:
            break

        ticket = {
            "ticket_id": raw["ticket_id"],
            "text": mask_pii(raw["text"]),
            "customer_id": raw["customer_id"],
            "order_id": raw["order_id"],
        }
        triage = {
            "intent": raw["intent"], "category": raw["category"],
            "confidence": 0.9, "requires_human": False,
        }

        try:
            final_state = await run_reply(ticket, triage)
        except Exception as e:
            print(f"[{i}/{len(candidates)}] {raw['ticket_id']} 실행 오류, 건너뜀: {e}")
            continue

        outcome = final_state["outcome"]
        print(f"[{i}/{len(candidates)}] {raw['ticket_id']} ({raw['intent']}) -> {outcome} (누적 {len(rows)}/{TARGET_COUNT})")

        if outcome != "auto_draft":
            continue

        rows.append({
            "golden_id": f"TONE-{len(rows) + 1:03d}",
            "ticket_id": raw["ticket_id"],
            "ticket_text": ticket["text"],
            "draft_text": final_state["draft"]["reply_text"],
            "human_tone_score": None,
        })

    if len(rows) < TARGET_COUNT:
        print(f"경고: 목표 {TARGET_COUNT}건 중 {len(rows)}건만 모았습니다(후보 풀 소진). "
              "필요하면 MAX_ATTEMPTS를 늘려 재실행하세요.")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"완료: {OUTPUT_PATH} — {len(rows)}건 (human_tone_score는 전부 null, 사용자가 채워야 함)")


if __name__ == "__main__":
    asyncio.run(main())
