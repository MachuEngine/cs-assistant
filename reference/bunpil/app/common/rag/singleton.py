from .embedder import BGEEmbedder
from .reranker import BGEReranker
from .retriever import RAGRetriever
from .store import RAGStore

_store: RAGStore = None
_embedder: BGEEmbedder = None
_reranker: BGEReranker = None
_retriever: RAGRetriever = None

"""
    * 싱글톤 패턴: 클래스의 인스턴스를 단 하나만 생성하도록 보장하고, 
    어디서든 이 인스턴스에 접근할 수 있는 전역적인 접근점을 제공하는 생성 디자인 패턴
    ---

    get_store() ───┐
    get_embedder()─┼─→ get_retriever() 조립 시 재사용
    get_reranker()─┘


    그러므로 호출부(app/main.py 등)는:
    retriever = get_retriever()   # 이거 하나만 부르면 전체 RAG 객체를 모두 생성함 
"""


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
