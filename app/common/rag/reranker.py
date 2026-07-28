import os

from FlagEmbedding import FlagReranker


class BGEReranker:
    def __init__(self):
        model_name = os.getenv("BGE_RERANK_MODEL", "BAAI/bge-reranker-base")
        # CPU에서만 수행 (DESIGN.md 8절)
        self.model = FlagReranker(model_name, use_fp16=False, devices=["cpu"])

    def rerank(self, query: str, passages: list[str], top_k: int = 5) -> list[dict]:
        pairs = [[query, p] for p in passages]
        scores = self.model.compute_score(pairs)
        if not isinstance(scores, list):
            scores = [scores]
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [{"index": idx, "score": s} for idx, s in ranked[:top_k]]
