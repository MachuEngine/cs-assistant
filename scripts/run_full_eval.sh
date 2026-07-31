#!/usr/bin/env bash
# 전체(--full) eval 6종을 한 번에 순서대로 실행한다 — NEXT_STEPS.md 우선순위 1.
#
# 비용 없는 3개(run_pii/run_retrieval/run_policy_violation)를 먼저 돌려 배선을
# 확인한 뒤, 실제 API 비용이 나가는 3개(run_triage/run_escalation/
# run_judge_reliability)로 넘어간다. 하나라도 실패하면 그 자리에서 멈춘다
# (set -e) — 뒤 단계에서 헛돈 쓰지 않기 위해서다.
#
# 사용법:
#   ./scripts/run_full_eval.sh          # 전부 실행
#   SKIP_TRIAGE=1 ./scripts/run_full_eval.sh   # 이미 돌렸으면 triage만 건너뛰기
#
# 전제: .env에 ANTHROPIC_API_KEY/OPENAI_API_KEY가 채워져 있을 것.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

set -a
source .env
set +a

export LLM_BACKEND="${LLM_BACKEND:-anthropic}"
export JUDGE_BACKEND="${JUDGE_BACKEND:-openai}"

run() {
    echo ""
    echo "=== $1 ($(date '+%H:%M:%S')) ==="
    .venv/bin/python "evals/runners/$1" --full
}

# --- 비용 $0 (순수 함수 / 로컬 임베딩) ---
run run_pii.py
run run_retrieval.py
run run_policy_violation.py

# --- 실제 API 비용 발생 ---
if [ -z "${SKIP_TRIAGE:-}" ]; then
    run run_triage.py
else
    echo ""
    echo "=== run_triage.py 건너뜀 (SKIP_TRIAGE=1) ==="
fi
run run_escalation.py
run run_judge_reliability.py

echo ""
echo "=== 전체 완료 — evals/reports/*.json 확인 ==="
ls -la evals/reports/
