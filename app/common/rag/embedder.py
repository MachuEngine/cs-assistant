import os

from FlagEmbedding import BGEM3FlagModel


class BGEEmbedder:
    def __init__(self):
        model_name = os.getenv("BGE_EMBED_MODEL", "BAAI/bge-m3")
        # CPU에서만 수행 (DESIGN.md 8절)
        self.model = BGEM3FlagModel(model_name, use_fp16=False, devices=["cpu"])

    def embed(self, texts: list[str], batch_size: int = 8) -> list[list[float]]:
        out = self.model.encode(texts, batch_size=batch_size, max_length=512)
        return out["dense_vecs"].tolist()
