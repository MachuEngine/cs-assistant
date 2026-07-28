"use client";

import { useState } from "react";

// --- 백엔드(app/main.py)와 고정된 SSE 이벤트 계약 ---------------------------
// 절대 임의로 바꾸지 않는다 — 바꾸려면 app/main.py + app/modules/reply/graph.py
// 의 stream_reply()도 함께 바꿔야 한다.

type TriageEvent = {
  status: "progress";
  stage: "triage";
  intent: string;
  category: string;
  confidence: number;
};

type StageEvent = {
  status: "progress";
  stage: "plan" | "agent" | "judge" | "validate";
};

type JudgeResult = {
  policy_compliance: number;
  tone: number;
  violations: { type: string; span: string; severity: string }[];
  reasoning: string;
};

type AutoDraftDone = {
  status: "done";
  outcome: "auto_draft";
  draft: string;
  cited_policies: string[];
  tools_used: string[];
  judge_result: JudgeResult;
};

type EscalatedDone = {
  status: "done";
  outcome: "escalated";
  escalation_reason: string;
};

type FailedEvent = {
  status: "error";
  outcome: "failed";
};

type SseEvent = TriageEvent | StageEvent | AutoDraftDone | EscalatedDone | FailedEvent;

// 사람이 읽기 쉬운 에스컬레이션 사유(DESIGN.md 3.1절 E1~E8과 동일한 표를
// UI 라벨용으로만 재사용 — 판정 로직은 전부 백엔드/routing.py 소관이다).
const ESCALATION_LABELS: Record<string, string> = {
  E1: "분류 확신도가 임계값 미만입니다",
  E2: "고객이 사람 상담원과의 연결을 요청했습니다",
  E3: "불만 접수 건이라 보상/책임 판단이 필요합니다",
  E4: "공격적인 표현이 감지되었습니다",
  E5: "에이전트가 스스로 처리 불가로 판단했습니다",
  E6: "문의한 주문을 찾을 수 없습니다",
  E7: "초안 저장 검증에 반복 실패했습니다",
  E8: "재시도 예산을 소진했지만 검증을 통과하지 못했습니다",
};

const DISCLAIMER =
  "This is a draft prepared by an AI assistant. A human agent is responsible for reviewing and approving it before it is sent.";

const STAGE_LABELS: Record<StageEvent["stage"], string> = {
  plan: "계획 수립",
  agent: "초안 작성(도구 호출)",
  judge: "채점(Judge)",
  validate: "검증",
};

type Outcome = "auto_draft" | "escalated" | "failed" | null;

export default function Home() {
  const [ticketText, setTicketText] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [triage, setTriage] = useState<TriageEvent | null>(null);
  const [stagesSeen, setStagesSeen] = useState<StageEvent["stage"][]>([]);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [draftResult, setDraftResult] = useState<AutoDraftDone | null>(null);
  const [escalationReason, setEscalationReason] = useState<string>("");

  function resetResult() {
    setTriage(null);
    setStagesSeen([]);
    setOutcome(null);
    setDraftResult(null);
    setEscalationReason("");
  }

  function handleEvent(event: SseEvent) {
    if (event.status === "progress" && event.stage === "triage") {
      setTriage(event);
      return;
    }
    if (event.status === "progress") {
      setStagesSeen((prev) => [...prev, event.stage]);
      return;
    }
    if (event.status === "done" && event.outcome === "auto_draft") {
      setDraftResult(event);
      setOutcome("auto_draft");
      return;
    }
    if (event.status === "done" && event.outcome === "escalated") {
      setEscalationReason(event.escalation_reason);
      setOutcome("escalated");
      return;
    }
    // status === "error"
    setOutcome("failed");
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!ticketText.trim() || submitting) return;

    resetResult();
    setSubmitting(true);

    try {
      const response = await fetch("/api/reply/stream", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ticket_text: ticketText, customer_id: customerId }),
      });

      if (!response.body) {
        setOutcome("failed");
        return;
      }

      // EventSource는 POST 바디를 지원하지 않아, fetch + 수동 SSE 파싱을 쓴다
      // (표준적인 hand-rolled SSE 클라이언트 패턴 — "data: {...}\n\n" 프레임을
      // 직접 분리한다).
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let sepIndex: number;
        while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, sepIndex);
          buffer = buffer.slice(sepIndex + 2);

          const line = frame.trim();
          if (!line.startsWith("data: ")) continue;

          try {
            const parsed = JSON.parse(line.slice("data: ".length)) as SseEvent;
            handleEvent(parsed);
          } catch {
            // 프레임 하나가 깨져도 스트림 전체를 죽이지 않는다.
            console.error("SSE 프레임 파싱 실패:", line);
          }
        }
      }
    } catch (err) {
      console.error("스트림 처리 중 오류:", err);
      setOutcome("failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col bg-zinc-50 dark:bg-black">
      <main className="mx-auto w-full max-w-2xl flex-1 px-6 py-10">
        <h1 className="mb-1 text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
          CS 티켓 어시스턴트 — 상담원 검토
        </h1>
        <p className="mb-8 text-sm text-zinc-500 dark:text-zinc-400">
          티켓 내용을 입력하면 분류 결과와 답변 초안(또는 에스컬레이션 사유)을
          실시간으로 확인할 수 있습니다.
        </p>

        <form onSubmit={handleSubmit} className="mb-8 flex flex-col gap-3">
          <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
            티켓 내용
            <textarea
              value={ticketText}
              onChange={(e) => setTicketText(e.target.value)}
              rows={5}
              placeholder="e.g. I want to cancel order ORD-000003, it hasn't shipped yet."
              className="mt-1 w-full rounded-md border border-zinc-300 bg-white p-3 text-sm text-zinc-900 shadow-sm focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50"
              disabled={submitting}
            />
          </label>

          <label className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
            고객 ID (선택)
            <input
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
              placeholder="CUST-000666"
              className="mt-1 w-full rounded-md border border-zinc-300 bg-white p-2 text-sm text-zinc-900 shadow-sm focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50"
              disabled={submitting}
            />
          </label>

          <button
            type="submit"
            disabled={submitting || !ticketText.trim()}
            className="self-start rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white shadow-sm disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-900"
          >
            {submitting ? "처리 중…" : "제출"}
          </button>
        </form>

        {triage && (
          <section className="mb-4 rounded-md border border-zinc-200 bg-white p-4 text-sm shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="mb-2 font-semibold text-zinc-800 dark:text-zinc-100">
              분류 결과
            </h2>
            <dl className="grid grid-cols-3 gap-2 text-zinc-600 dark:text-zinc-300">
              <div>
                <dt className="text-xs text-zinc-400">인텐트</dt>
                <dd className="font-mono">{triage.intent}</dd>
              </div>
              <div>
                <dt className="text-xs text-zinc-400">카테고리</dt>
                <dd className="font-mono">{triage.category}</dd>
              </div>
              <div>
                <dt className="text-xs text-zinc-400">확신도</dt>
                <dd className="font-mono">{triage.confidence.toFixed(2)}</dd>
              </div>
            </dl>
          </section>
        )}

        {stagesSeen.length > 0 && (
          <section className="mb-4 rounded-md border border-zinc-200 bg-white p-4 text-sm shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="mb-2 font-semibold text-zinc-800 dark:text-zinc-100">
              진행 상황
            </h2>
            <ol className="flex flex-wrap gap-2">
              {stagesSeen.map((stage, i) => (
                <li
                  key={`${stage}-${i}`}
                  className="rounded-full bg-zinc-100 px-3 py-1 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
                >
                  {STAGE_LABELS[stage]}
                </li>
              ))}
            </ol>
          </section>
        )}

        {outcome === "auto_draft" && draftResult && (
          <section className="mb-4 rounded-md border border-emerald-200 bg-emerald-50 p-4 text-sm shadow-sm dark:border-emerald-900 dark:bg-emerald-950/30">
            <h2 className="mb-2 font-semibold text-emerald-900 dark:text-emerald-200">
              답변 초안
            </h2>
            <pre className="mb-3 whitespace-pre-wrap font-sans text-zinc-800 dark:text-zinc-100">
              {draftResult.draft}
            </pre>

            <div className="mb-2">
              <span className="text-xs font-medium text-zinc-500">
                인용된 정책 조항:{" "}
              </span>
              {draftResult.cited_policies.length > 0 ? (
                draftResult.cited_policies.map((p) => (
                  <span
                    key={p}
                    className="mr-1 rounded bg-zinc-200 px-2 py-0.5 font-mono text-xs dark:bg-zinc-700"
                  >
                    {p}
                  </span>
                ))
              ) : (
                <span className="text-xs text-zinc-400">없음</span>
              )}
            </div>

            <div className="mb-3">
              <span className="text-xs font-medium text-zinc-500">
                사용한 도구:{" "}
              </span>
              {draftResult.tools_used.map((t) => (
                <span
                  key={t}
                  className="mr-1 rounded bg-zinc-200 px-2 py-0.5 font-mono text-xs dark:bg-zinc-700"
                >
                  {t}
                </span>
              ))}
            </div>

            <div className="flex gap-2">
              {/* 목업 버튼 — 실제 저장/발송 백엔드는 아직 없다 */}
              <button
                type="button"
                title="아직 실제 저장/발송 기능은 연결되지 않았습니다(목업)"
                className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-700 dark:border-zinc-700 dark:text-zinc-200"
              >
                편집
              </button>
              <button
                type="button"
                title="아직 실제 저장/발송 기능은 연결되지 않았습니다(목업)"
                className="rounded-md bg-emerald-700 px-3 py-1.5 text-xs font-medium text-white"
              >
                승인
              </button>
            </div>
          </section>
        )}

        {outcome === "escalated" && (
          <section className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-4 text-sm shadow-sm dark:border-amber-900 dark:bg-amber-950/30">
            <h2 className="mb-1 font-semibold text-amber-900 dark:text-amber-200">
              사람 상담원에게 에스컬레이션됨
            </h2>
            <p className="text-amber-800 dark:text-amber-200">
              사유 ({escalationReason}):{" "}
              {ESCALATION_LABELS[escalationReason] ?? "알 수 없는 사유"}
            </p>
            {/* 초안 필드는 존재하지도, 렌더링되지도 않는다 — 흐릿하게라도
                보여주지 않는다(하드 요구사항). */}
          </section>
        )}

        {outcome === "failed" && (
          <section className="mb-4 rounded-md border border-red-200 bg-red-50 p-4 text-sm shadow-sm dark:border-red-900 dark:bg-red-950/30">
            <h2 className="mb-1 font-semibold text-red-900 dark:text-red-200">
              오류가 발생했습니다
            </h2>
            <p className="text-red-800 dark:text-red-200">
              요청을 처리하지 못했습니다. 잠시 후 다시 시도해주세요.
            </p>
          </section>
        )}
      </main>

      {/* 상시 고지 — outcome과 무관하게 항상 보인다(CLAUDE.md 보안 하드룰 6). */}
      <footer className="border-t border-zinc-200 bg-white px-6 py-4 text-center text-xs text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
        {DISCLAIMER}
        <br />본 화면의 초안은 AI가 준비한 것이며, 발송 전 상담원의 최종 검토와
        승인이 필요합니다.
      </footer>
    </div>
  );
}
