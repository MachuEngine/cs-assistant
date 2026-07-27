import os

from FlagEmbedding import BGEM3FlagModel


class BGEEmbedder:
    def __init__(self):
        model_name = os.getenv("BGE_EMBED_MODEL", "BAAI/bge-m3")
        # 모델을 메모리에 로드 - 오래 걸리는 작업으로 객체 생성 시 단 1번 실행 후 self.model에 저장
        self.model = BGEM3FlagModel(model_name, use_fp16=False)

    def embed(self, texts: list[str], batch_size: int = 8) -> list[list[float]]:
        # 실제 임배딩 계산이 이뤄짐 
        out = self.model.encode(texts, batch_size=batch_size, max_length=512)
        return out["dense_vecs"].tolist()
    
    """
        batch_size=8은 "한 번에 모델에 넣을 텍스트 묶음 크기" 
        — 텍스트가 100개라면 8개씩 12~13번 나눠서 처리(메모리 초과 방지). 
        - max_length=512는 텍스트가 이보다 길면 잘라낸다는 뜻(토큰 기준) 

        encode()의 반환 값 out은 딕셔너리.
        GE-M3 모델의 특징: "dense(밀집) 벡터 + sparse(희소) 벡터 + multi-vector"를 동시에!
        즉 out = dense vector + sparse vector + multi vector 이고, 
        그 중 dense 벡터만 사용 함. 

        out = {
            "dense_vecs": [[0.1, 0.2, ...], [0.5, 0.3, ...], [0.2, 0.9, ...]],  # 텍스트 3개분
            "sparse_vecs": {...},   # 텍스트 3개분 (다른 형태)
            "colbert_vecs": [...],  # 텍스트 3개분 (또 다른 형태)
        }
    """
