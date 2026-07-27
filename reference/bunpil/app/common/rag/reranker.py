import os

from FlagEmbedding import FlagReranker

"""
    query + passages(20개)
        → [질문, 후보] 쌍 20개 생성
        → 모델이 각 쌍의 관련도 점수 계산 (scores)
        → 점수 기준 내림차순 정렬 (원래 인덱스는 유지)
        → 상위 top_k개만 {"index":..., "score":...} 형태로 반환
"""

class BGEReranker:
    def __init__(self):
        model_name = os.getenv("BGE_RERANK_MODEL", "BAAI/bge-reranker-base")
        self.model = FlagReranker(model_name, use_fp16=False)

    def rerank(self, query: str, passages: list[str], top_k: int = 5) -> list[dict]:
        pairs = [[query, p] for p in passages]
        scores = self.model.compute_score(pairs) # rerank 스코어 계산 
        if not isinstance(scores, list): # passages가 만약 1개면 list가 아니지만 list 형식으로 맞춰줌 
            scores = [scores]
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [{"index": idx, "score": s} for idx, s in ranked[:top_k]]
    
    """
    rerank 모델은 입력 쿼리와 후보 20개 passage를 각각 쌍으로 매칭해서 입력함 
    -> 그렇기 때문에 오래 걸리는 대신 정확함. (BGEEmbedder와 차이점)

    pairs = [
        ["사회계약론을 주장한 사상가는?", "사회계약론은 홉스, 로크, 루소가..."],
        ["사회계약론을 주장한 사상가는?", "삼권분립은 입법·행정·사법을..."],
        ["사회계약론을 주장한 사상가는?", "기본권은 자유권, 평등권..."],
        ...
    ]
    
    scores = [
        score0,
        score1,
        score2,
        ...   
    ]

    * Sorting 
        - enumerate(scores) -> (0, score0), (1, score1), (2, score2), ...
        - key = lambda x: x[1] -> 튜플 점수를 튜플의 두번째 원소 기준으로 정렬하라 
        - reverse=True -> 내림차순 정렬
    """
