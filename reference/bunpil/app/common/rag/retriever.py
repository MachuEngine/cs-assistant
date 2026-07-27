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
    
        """
        candidates = [
            {"text": "사회계약론은 홉스, 로크, 루소가...", "metadata": {"source": "정치.pdf"}, "distance": 0.15},  # index 0
            {"text": "삼권분립은 입법·행정·사법을...", "metadata": {"source": "정치.pdf"}, "distance": 0.22},      # index 1
            {"text": "기본권은 자유권, 평등권...",      "metadata": {"source": "헌법.pdf"}, "distance": 0.31},      # index 2
        ]

        passages = [
            "사회계약론은 홉스, 로크, 루소가...",   # index 0
            "삼권분립은 입법·행정·사법을...",      # index 1
            "기본권은 자유권, 평등권...",         # index 2
        ]

        ranked = [
            {"index": 0, "score": 0.95},   # candidates[0]이 가장 관련 높다고 재평가됨
            {"index": 2, "score": 0.60},   # candidates[2]가 두 번째
        ]

        return = [
            {
                candidates[0]["text"]      # "사회계약론은 홉스, 로크, 루소가..."
                candidates[0]["metadata"]  # {"source": "정치.pdf"}
                r["score"]                 # 0.95
            },
            {
                candidates[2]["text"]      # "기본권은 자유권, 평등권..."
                candidates[2]["metadata"]  # {"source": "헌법.pdf"}
                r["score"]                 # 0.60
            }
        ]

        """