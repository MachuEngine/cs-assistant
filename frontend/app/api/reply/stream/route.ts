import { NextRequest } from "next/server";

// SSE는 서버가 흘려보내는 대로 그대로 통과시켜야 하므로, 이 라우트는 캐시되거나
// 정적으로 최적화되면 안 된다.
export const dynamic = "force-dynamic";

const encoder = new TextEncoder();

function sseErrorStream(): ReadableStream<Uint8Array> {
  // FastAPI 백엔드 계약(app/main.py)과 동일한 실패 이벤트 모양을 재사용한다 —
  // 브라우저 쪽 파서는 항상 이 하나의 형태만 알면 된다.
  return new ReadableStream({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          `data: ${JSON.stringify({ status: "error", outcome: "failed" })}\n\n`
        )
      );
      controller.close();
    },
  });
}

/**
 * 브라우저가 유일하게 호출하는 엔드포인트. 서버 간 CS_API_KEY는 여기서만
 * 붙이고 브라우저에는 절대 내려보내지 않는다(DESIGN.md 13절 배포 구성).
 *
 * FastAPI POST /reply/stream 의 응답 본문(SSE 바이트 스트림)을 파싱하지
 * 않고 그대로 pass-through 한다 — 이벤트 형태를 여기서 한 번 더 손대면
 * 백엔드와 계약이 갈라질 여지가 생긴다.
 */
export async function POST(request: NextRequest) {
  const apiKey = process.env.CS_API_KEY;
  const baseUrl = process.env.FASTAPI_BASE_URL ?? "http://localhost:8000";

  if (!apiKey) {
    console.error("CS_API_KEY가 설정되지 않았습니다 — frontend/.env.local 확인");
    return new Response(sseErrorStream(), {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return new Response(sseErrorStream(), {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${baseUrl}/reply/stream`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-API-Key": apiKey,
      },
      body: JSON.stringify(body),
    });
  } catch (err) {
    // FastAPI에 도달하지 못함(포트 오류, 서버 다운 등) — 내부 상세는 로그에만.
    console.error("FastAPI /reply/stream 연결 실패:", err);
    return new Response(sseErrorStream(), {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  }

  if (!upstream.ok || !upstream.body) {
    console.error("FastAPI /reply/stream 비정상 응답:", upstream.status);
    return new Response(sseErrorStream(), {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
    },
  });
}
