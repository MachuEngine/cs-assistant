#!/usr/bin/env python3
"""retrieval_golden.jsonl 기반 RAG retrieval 평가 — DESIGN.md 6.2절 🟢.

get_retriever()(Phase 3, BGE-M3 + BGE-reranker)를 그대로 재사용한다.
LLM 호출 없음 — 임베딩/리랭크만 쓰므로 --full로 돌려도 API 비용은 없다
(다만 CPU 모델 로드 시간은 있음).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _common import load_jsonl, parse_args, select_sample, write_report  # noqa: E402
from app.common.rag.singleton import get_retriever  # noqa: E402

GOLDEN_PATH = "evals/golden/retrieval_golden.jsonl"
TOP_K = 5


def main() -> None:
    args = parse_args(default_sample=20)
    golden = load_jsonl(GOLDEN_PATH)
    sample = select_sample(golden, args)
    retriever = get_retriever()

    recall_hits = []  # 행별 partial recall (expected 중 몇 % 가 top-5에 있었나)
    strict_hits = []  # 행별 expected 전부가 top-5에 있었는가
    reciprocal_ranks = []
    misses = []

    for i, row in enumerate(sample, start=1):
        results = retriever.retrieve(row["query"], "policies", top_k=TOP_K)
        got_ids = [r["metadata"]["clause_id"] for r in results]
        expected = set(row["expected_clause_ids"])

        found = expected & set(got_ids)
        recall_hits.append(len(found) / len(expected) if expected else 1.0)
        strict_hits.append(found == expected)

        rr = 0.0
        for rank, cid in enumerate(got_ids, start=1):
            if cid in expected:
                rr = 1.0 / rank
                break
        reciprocal_ranks.append(rr)

        if found != expected:
            misses.append({"golden_id": row.get("golden_id"), "query": row["query"],
                            "expected": list(expected), "got": got_ids})

        print(f"[{i}/{len(sample)}] {row.get('golden_id')} expected={sorted(expected)} got={got_ids}")

    n = len(sample)
    recall_at_5 = sum(recall_hits) / n if n else None
    strict_recall_at_5 = sum(strict_hits) / n if n else None
    mrr = sum(reciprocal_ranks) / n if n else None

    report = {
        "n": n,
        "recall_at_5_partial": recall_at_5,
        "recall_at_5_strict_all_expected": strict_recall_at_5,
        "mrr": mrr,
        "misses": misses,
    }
    path = write_report("run_retrieval", report)

    print(f"\nrecall@5(partial)={recall_at_5} recall@5(strict)={strict_recall_at_5} mrr={mrr}")
    print(f"리포트 저장: {path}")


if __name__ == "__main__":
    main()
