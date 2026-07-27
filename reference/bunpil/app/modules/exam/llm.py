import os

from langchain_ollama import ChatOllama

from app.common.llm.backends.chat_runpod import ChatRunPod


def get_langchain_model(temperature: float = 0.7):
    """LangGraph ReAct 에이전트용 LangChain 호환 LLM을 반환한다.

    temperature 기본값 0.7 — ChatRunPod 기존 기본값과 동일하게 맞춰 두 백엔드가
    같은 조건으로 비교되도록 함(2026-07-10 temperature A/B 테스트 근거, TROUBLESHOOTING.md 참고).
    
    * temperature: 언어 모델이 답변을 만들 때 답변의 무작위성(randomness)와
                    창의성(creativity)를 조절하는 파라미터.
    """
    backend = os.getenv("LLM_BACKEND", "local")
    if backend == "local":
        return ChatOllama(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "qwen2.5:14b-instruct"),
            num_predict=2048,  # RunPod 백엔드(max_tokens=2048)와 동일 캡 — 미설정 시 폭주 생성 위험
            num_ctx=16384,  # Ollama 기본값(4096)은 멀티턴 ReAct+RAG 검색 결과 누적 시 쉽게 초과되어
            # 컨텍스트가 잘리고 모델이 시스템 프롬프트를 잃어 응답이 깨지는 원인이 됨(2026-07-09 확인).
            # 모델 자체는 32K 네이티브 지원(RunPod도 이에 가깝게 운용) — 로컬 개발 환경만 좁게 도는 격차였음.
            temperature=temperature,
        )
    if backend == "runpod":
        return ChatRunPod(temperature=temperature)
    if backend == "openai":
        from app.common.llm.backends.chat_openai import ChatOpenAIBackend
        return ChatOpenAIBackend(temperature=temperature)
    raise NotImplementedError(f"지원하지 않는 LLM_BACKEND: '{backend}'")
