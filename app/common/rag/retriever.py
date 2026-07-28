from .embedder import BGEEmbedder
from .reranker import BGEReranker
from .store import RAGStore


class RAGRetriever:
    def __init__(self, store: RAGStore, embedder: BGEEmbedder, reranker: BGEReranker):
        self.store = store
        self.embedder = embedder
        self.reranker = reranker

    def retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        n_candidates: int = 20,
    ) -> list[dict]:
        query_vec = self.embedder.embed([query])[0]
        candidates = self.store.query(collection_name, query_vec, n_results=n_candidates)
        if not candidates:
            return []
        passages = [c["text"] for c in candidates]
        ranked = self.reranker.rerank(query, passages, top_k=top_k)
        return [
            {
                "text": candidates[r["index"]]["text"],
                "metadata": candidates[r["index"]]["metadata"],
                "score": r["score"],
            }
            for r in ranked
        ]
