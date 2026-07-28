from langchain_text_splitters import RecursiveCharacterTextSplitter

# 문장·문단 경계를 우선 존중하며 분할하기 위한 구분자 우선순위(영문 기준).
_SEPARATORS = ["\n\n", "\n", ". ", ".\n", "! ", "? ", " ", ""]

# DESIGN.md 3.3절: 청킹 300~500 토큰, overlap 50. 실제 토크나이저 로드 전이라
# 영어 기준 1토큰≈0.75단어의 근사치로 조항이 이 임계값을 넘는지 판단한다.
_MAX_CLAUSE_WORDS = 375  # ≈ 500 토큰
_CHUNK_CHARS = 500 * 4  # 토큰당 ~4자 근사치
_OVERLAP_CHARS = 50 * 4


def chunk_document(doc: dict) -> list[dict]:
    """조항을 청크로 변환한다.

    조항 단위를 우선한다 — 대부분의 조항은 그대로 청크 1개가 된다. 조항이
    길면(약 500토큰 초과) 문장 경계로 나눠 여러 청크로 만들되, 각 청크 앞에
    조항 헤더(`[RET-02] Return Window by Tier`)를 반복 삽입해 어느 조각으로
    나뉘든 인용 정확도가 유지되게 한다(save_draft 게이트 ④가 이 헤더에 의존).
    """
    chunks = []
    for clause in doc["clauses"]:
        header = f"[{clause['clause_id']}] {clause['title']}"
        word_count = len(clause["body"].split())

        if word_count <= _MAX_CLAUSE_WORDS:
            pieces = [clause["body"]]
        else:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=_CHUNK_CHARS,
                chunk_overlap=_OVERLAP_CHARS,
                separators=_SEPARATORS,
                length_function=len,
            )
            pieces = splitter.split_text(clause["body"])

        for i, piece in enumerate(pieces):
            chunks.append({
                "text": f"{header}\n{piece}",
                "source": doc["source"],
                "clause_id": clause["clause_id"],
                "title": clause["title"],
                "chunk_index": i,
            })
    return chunks
