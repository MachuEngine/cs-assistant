import os

from .backends.ollama import OllamaBackend
from .backends.openai import OpenAIBackend
from .backends.runpod import RunPodBackend
from .base import LLMBackend


def get_llm_backend() -> LLMBackend:
    # 새 백엔드(local 이외) 추가 시 app/common/llm/tracing.py의 _PROD_BACKENDS도 확인 —
    # 거기서 실제 서빙 백엔드인지(dev/prod LangSmith 분기 기준)를 별도로 판단한다.
    backend = os.getenv("LLM_BACKEND", "local")
    if backend == "runpod":
        return RunPodBackend()
    if backend == "openai":
        return OpenAIBackend()
    return OllamaBackend()


def get_judge_backend() -> LLMBackend:
    # LLM_BACKEND(생성용)와 독립 — Judge만 별도로 OpenAI 등으로 바꿔보고 싶을 때 사용.
    # 미설정 시 기존과 동일하게 항상 Ollama(OLLAMA_JUDGE_MODEL, 폴백 OLLAMA_MODEL).
    if os.getenv("JUDGE_BACKEND", "local") == "openai":
        return OpenAIBackend(model=os.getenv("OPENAI_JUDGE_MODEL"))
    judge_model = os.getenv("OLLAMA_JUDGE_MODEL")
    if judge_model:
        return OllamaBackend(model=judge_model)
    return OllamaBackend()  # OLLAMA_MODEL 폴백
