"""FastAPI 엔트리포인트. DESIGN.md 7절 API 계약.

라우트: GET /health(무인증) · POST /triage · POST /reply · POST /reply/stream(SSE).
/health를 제외한 전 엔드포인트는 X-API-Key 헤더를 CS_API_KEY와 비교해 인증한다
(hmac.compare_digest로 타이밍 공격 방지).

[엄수] 이 파일은 파이프라인 로직을 담지 않는다 — triage/reply 모듈을 호출만
한다. mask_pii/에스컬레이션 판정 등은 각 모듈이 이미 수행하므로 여기서
중복하지 않는다.
"""
import asyncio
import hmac
import json
import logging
import os
import re
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# 다른 모듈이 os.getenv로 LLM_BACKEND/API 키 등을 읽기 전에 .env를 로드한다
# (app.common.llm 팩토리 등은 호출 시점에 지연 조회하므로 여기서 한 번만 하면 된다).
load_dotenv()

from app.common.privacy import mask_pii  # noqa: E402 (load_dotenv 이후 import)
from app.modules.reply.graph import run_reply, stream_reply  # noqa: E402
from app.modules.triage.classifier import triage_ticket  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(title="CS 티켓 어시스턴트")

# --- 요청 크기 제한 미들웨어 ------------------------------------------------
# BaseHTTPMiddleware는 응답을 버퍼링해 StreamingResponse(SSE)를 깨뜨릴 수 있어
# 순수 ASGI 미들웨어로 작성한다. Content-Length 헤더만 검사한다 — 이 프로젝트의
# 요청 본문은 짧은 티켓 텍스트뿐이라 청크 전송(무헤더) 우회까지 막을 필요는
# 낮다고 판단(알려진 단순화, HARNESS_ENGINEERING.md에 기록하지 않음 — 코드
# 주석으로 충분).
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(32 * 1024)))


class RequestSizeLimitMiddleware:
    def __init__(self, asgi_app, max_bytes: int):
        self.app = asgi_app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                too_large = int(content_length) > self.max_bytes
            except ValueError:
                too_large = False
            if too_large:
                response = JSONResponse({"detail": "request body too large"}, status_code=413)
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


app.add_middleware(RequestSizeLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)

# --- 동시 요청 제한(DESIGN.md 7절) ------------------------------------------
# /reply, /reply/stream만 제한한다 — 멀티턴 에이전트라 비용·시간이 크다.
# /triage는 단일 호출이라 대상 아님.
_REPLY_CONCURRENCY = int(os.getenv("REPLY_CONCURRENCY_LIMIT", "2"))
_reply_semaphore = asyncio.Semaphore(_REPLY_CONCURRENCY)

_ORDER_ID_RE = re.compile(r"\bORD-\d{6}\b")


class TriageRequest(BaseModel):
    ticket_text: str
    flags: str = ""


class ReplyRequest(BaseModel):
    ticket_text: str
    customer_id: str = ""
    flags: str = ""


def _check_api_key(x_api_key: str | None) -> None:
    expected = os.getenv("CS_API_KEY", "")
    # 비교 대상이 없거나(설정 누락) 헤더가 없으면 무조건 거부 — "인증 생략"으로
    # 새지 않는다. hmac.compare_digest로 타이밍 사이드채널을 막는다.
    if not expected or not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def _extract_order_id(raw_text: str) -> str:
    """티켓 본문에서 주문번호를 추출한다(예: "ORD-000123").

    API 계약(DESIGN.md 7절)의 /reply 요청 바디에는 order_id 필드가 없다 —
    실제 CS 상황처럼 상담원(여기서는 에이전트)이 고객 메시지에서 직접
    읽어낸다. 못 찾으면 빈 문자열로 두고, lookup_order가 필요한 인텐트라면
    에이전트가 스스로 order_not_found(E6) 경로를 타거나 고객에게 재확인을
    요구하는 초안을 쓰게 된다.
    """
    m = _ORDER_ID_RE.search(raw_text)
    return m.group(0) if m else ""


def _shape_reply_result(final_state: dict, triage: dict) -> dict:
    """run_reply()/stream_reply()의 최종 상태를 API 응답 형태로 변환한다."""
    if final_state["outcome"] == "escalated":
        return {
            "outcome": "escalated",
            "escalation_reason": final_state["escalation_reason"],
            "triage": triage,
        }
    return {
        "outcome": final_state["outcome"],
        "draft": final_state["draft"]["reply_text"],
        "cited_policies": final_state["draft"]["cited_policies"],
        "tools_used": final_state["draft"]["tools_used"],
        "judge_result": final_state["judge_result"],
        "triage": triage,
    }


def _public_triage(triage: dict) -> dict:
    """DESIGN.md 7절 /triage 응답 계약과 동일한 필드만 노출한다."""
    return {
        "intent": triage["intent"],
        "category": triage["category"],
        "confidence": triage["confidence"],
        "requires_human": triage["requires_human"],
        "reason": triage["reason"],
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/triage")
async def triage_endpoint(body: TriageRequest, x_api_key: str | None = Header(None, alias="X-API-Key")) -> dict:
    _check_api_key(x_api_key)
    triage = await triage_ticket(body.ticket_text, body.flags)
    return _public_triage(triage)


@app.post("/reply")
async def reply_endpoint(body: ReplyRequest, x_api_key: str | None = Header(None, alias="X-API-Key")) -> dict:
    _check_api_key(x_api_key)

    if _reply_semaphore.locked():
        raise HTTPException(status_code=429, detail="too many concurrent reply requests, try again shortly")

    async with _reply_semaphore:
        try:
            triage = await triage_ticket(body.ticket_text, body.flags)
            public_triage = _public_triage(triage)

            if triage["escalation_reason"]:
                return {
                    "outcome": "escalated",
                    "escalation_reason": triage["escalation_reason"],
                    "triage": public_triage,
                }

            ticket = {
                "ticket_id": f"REQ-{uuid.uuid4().hex[:12]}",
                "text": mask_pii(body.ticket_text),
                "customer_id": body.customer_id,
                "order_id": _extract_order_id(body.ticket_text),
            }
            triage_info = {
                "intent": triage["intent"],
                "category": triage["category"],
                "confidence": triage["confidence"],
                "requires_human": triage["requires_human"],
            }
            final_state = await run_reply(ticket, triage_info)
            return _shape_reply_result(final_state, public_triage)
        except Exception:
            # 내부 예외 상세를 응답에 노출하지 않는다(CLAUDE.md). 티켓 본문도
            # 로그에 남기지 않는다.
            logger.exception("POST /reply 파이프라인 오류")
            return {"outcome": "failed"}


async def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _reply_stream_events(ticket_text: str, customer_id: str, flags: str):
    """SSE 이벤트 제너레이터. 이벤트 형태는 프론트엔드(frontend/app/api/reply/stream)와
    계약이 고정돼 있으므로 임의로 바꾸지 않는다."""
    try:
        triage = await triage_ticket(ticket_text, flags)
        yield await _sse_event({
            "status": "progress",
            "stage": "triage",
            "intent": triage["intent"],
            "category": triage["category"],
            "confidence": triage["confidence"],
        })

        if triage["escalation_reason"]:
            yield await _sse_event({
                "status": "done",
                "outcome": "escalated",
                "escalation_reason": triage["escalation_reason"],
            })
            return

        ticket = {
            "ticket_id": f"REQ-{uuid.uuid4().hex[:12]}",
            "text": mask_pii(ticket_text),
            "customer_id": customer_id,
            "order_id": _extract_order_id(ticket_text),
        }
        triage_info = {
            "intent": triage["intent"],
            "category": triage["category"],
            "confidence": triage["confidence"],
            "requires_human": triage["requires_human"],
        }

        async for event in stream_reply(ticket, triage_info):
            yield await _sse_event(event)
    except Exception:
        logger.exception("POST /reply/stream 파이프라인 오류")
        yield await _sse_event({"status": "error", "outcome": "failed"})


@app.post("/reply/stream")
async def reply_stream_endpoint(
    body: ReplyRequest, x_api_key: str | None = Header(None, alias="X-API-Key")
) -> StreamingResponse:
    _check_api_key(x_api_key)

    if _reply_semaphore.locked():
        raise HTTPException(status_code=429, detail="too many concurrent reply requests, try again shortly")

    async def gen():
        async with _reply_semaphore:
            async for chunk in _reply_stream_events(body.ticket_text, body.customer_id, body.flags):
                yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream")
