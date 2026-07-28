#!/usr/bin/env python3
"""합성 정책 문서를 파싱·청킹·임베딩해 ChromaDB 'policies' 컬렉션에 적재한다.

영구 컬렉션에는 정책 문서만 적재한다 — 티켓 본문은 절대 적재하지 않는다
(DESIGN.md 4.2절 / CLAUDE.md 핵심 컨벤션). 이미 적재된 문서는 자동 스킵한다.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.common.rag.chunker import chunk_document  # noqa: E402
from app.common.rag.parser import parse_policy_doc  # noqa: E402
from app.common.rag.singleton import get_embedder, get_store  # noqa: E402

POLICIES_DIR = pathlib.Path("data/synthetic/policies")
COLLECTION_NAME = "policies"


def main() -> None:
    store = get_store()
    already_indexed = store.indexed_sources(COLLECTION_NAME)

    all_chunks = []
    for path in sorted(POLICIES_DIR.glob("*.md")):
        doc = parse_policy_doc(str(path))
        if doc["source"] in already_indexed:
            print(f"스킵(이미 적재됨): {doc['source']}")
            continue
        chunks = chunk_document(doc)
        all_chunks.extend(chunks)
        print(f"파싱: {doc['source']} — 조항 {len(doc['clauses'])}개 → 청크 {len(chunks)}개")

    if not all_chunks:
        print("적재할 신규 청크 없음")
        return

    print(f"임베딩 계산 중... ({len(all_chunks)}개 청크)")
    embedder = get_embedder()
    embeddings = embedder.embed([c["text"] for c in all_chunks])

    store.add_chunks(COLLECTION_NAME, all_chunks, embeddings)
    print(f"완료: '{COLLECTION_NAME}' 컬렉션에 {len(all_chunks)}개 청크 적재")
    print(f"컬렉션 총 개수: {store.count(COLLECTION_NAME)}")


if __name__ == "__main__":
    main()
