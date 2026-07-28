from .embedder import BGEEmbedder
from .reranker import BGEReranker
from .retriever import RAGRetriever
from .store import RAGStore

_store: RAGStore | None = None
_embedder: BGEEmbedder | None = None
_reranker: BGEReranker | None = None
_retriever: RAGRetriever | None = None


def get_store() -> RAGStore:
    global _store
    if _store is None:
        _store = RAGStore()
    return _store


def get_embedder() -> BGEEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = BGEEmbedder()
    return _embedder


def get_reranker() -> BGEReranker:
    global _reranker
    if _reranker is None:
        _reranker = BGEReranker()
    return _reranker


def get_retriever() -> RAGRetriever:
    global _retriever
    if _retriever is None:
        _retriever = RAGRetriever(get_store(), get_embedder(), get_reranker())
    return _retriever
