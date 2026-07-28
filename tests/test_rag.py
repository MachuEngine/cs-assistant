"""RAG 인프라 스모크 테스트 (Phase 3 완료 기준).

정책 문서가 'policies' 컬렉션에 이미 적재돼 있다고 가정한다
(scripts/index_policies.py 를 먼저 실행). 모델 로드가 필요해 무겁다 —
CI의 경량 스모크 테스트 대상이 아니다(DESIGN.md 13절).
"""
import pytest

from app.common.rag.singleton import get_retriever, get_store

COLLECTION_NAME = "policies"

pytestmark = pytest.mark.rag


def test_policies_collection_indexed():
    store = get_store()
    assert store.count(COLLECTION_NAME) > 0, (
        "policies 컬렉션이 비어 있다 — 먼저 scripts/index_policies.py 를 실행할 것"
    )


def test_retrieve_returns_relevant_clause():
    retriever = get_retriever()
    results = retriever.retrieve(
        "the return window already passed, can I still get a refund?",
        COLLECTION_NAME,
        top_k=3,
    )

    assert results, "검색 결과가 비어 있다"
    for r in results:
        assert "clause_id" in r["metadata"]
        assert "source" in r["metadata"]

    clause_ids = [r["metadata"]["clause_id"] for r in results]
    # RET-02(등급별 반품 기한)가 이 질의에 가장 직접적으로 답하는 조항이다.
    assert "RET-02" in clause_ids, f"기대한 조항(RET-02)이 top-3에 없음: {clause_ids}"
