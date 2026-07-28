import os

import chromadb
from chromadb.config import Settings


class RAGStore:
    def __init__(self):
        persist_dir = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(
                anonymized_telemetry=False,
                chroma_product_telemetry_impl="app.common.rag.telemetry.NoOpProductTelemetry",
            ),
        )

    def _collection(self, name: str):
        return self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(
        self,
        collection_name: str,
        chunks: list[dict],
        embeddings: list[list[float]],
    ) -> None:
        col = self._collection(collection_name)
        ids = [f"{c['source']}_{c['clause_id']}_{c['chunk_index']}" for c in chunks]
        metas = [
            {"source": c["source"], "clause_id": c["clause_id"], "title": c["title"]}
            for c in chunks
        ]
        col.upsert(
            documents=[c["text"] for c in chunks],
            embeddings=embeddings,
            metadatas=metas,
            ids=ids,
        )

    def indexed_sources(self, collection_name: str) -> set[str]:
        """컬렉션에 이미 적재된 source(문서명) 목록을 반환한다. 재인덱싱 스킵에 쓴다."""
        try:
            col = self._collection(collection_name)
            if col.count() == 0:
                return set()
            result = col.get(include=["metadatas"])
            return {m.get("source", "") for m in result["metadatas"]}
        except Exception:
            return set()

    def count(self, collection_name: str) -> int:
        try:
            return self._collection(collection_name).count()
        except Exception:
            return 0

    def query(
        self,
        collection_name: str,
        query_embedding: list[float],
        n_results: int = 20,
    ) -> list[dict]:
        col = self._collection(collection_name)
        res = col.query(query_embeddings=[query_embedding], n_results=n_results)
        if not res["documents"][0]:
            return []
        return [
            {"text": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                res["documents"][0], res["metadatas"][0], res["distances"][0]
            )
        ]
