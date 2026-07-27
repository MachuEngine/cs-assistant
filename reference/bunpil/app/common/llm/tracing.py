"""LangSmith 프로젝트를 LLM_BACKEND에 따라 dev/prod로 자동 분기.

로컬(Ollama)과 프로덕션(RunPod/OpenAI) 트레이스가 같은 LangSmith 프로젝트로 섞이면,
로컬 개발 중 발생하는 노이즈(실험적 프롬프트 변경·재시도·모델 비교 실험 등)가
프로덕션 통계를 오염시킨다. .env에 LANGCHAIN_PROJECT를 정적으로 박아두고 사람이
환경마다 다르게 관리하는 방식은 실수로 같은 값이 배포될 위험이 있어, 대신
LLM_BACKEND 값을 보고 매 실행 시점에 코드가 자동으로 결정한다.

호출 시점: load_dotenv() 직후, LangChain 트레이스가 발생할 수 있는 모든
진입점(app/main.py, evals/eval_*.py 등)에서 다른 로직보다 먼저 호출한다.
"""
import os

# 실제 서빙(프로덕션) 백엔드 — local(Ollama)만 순수 로컬 개발용이고 나머지는 전부 실제
# 트래픽일 수 있다고 간주. app/common/llm/factory.py에 새 백엔드가 추가되면 여기도
# 같이 갱신해야 한다(반대쪽에도 참조 주석 있음).
_PROD_BACKENDS = {"runpod", "openai"}


def init_langsmith_project() -> None:
    if os.getenv("LANGCHAIN_TRACING_V2") != "true":
        return
    base = os.getenv("LANGCHAIN_PROJECT", "bunpil")
    if base != "bunpil":
        return  # 기본값이 아닌 값이 명시적으로 설정됨 — override로 간주, 그대로 사용
    backend = os.getenv("LLM_BACKEND", "local")
    suffix = "prod" if backend in _PROD_BACKENDS else "dev"
    os.environ["LANGCHAIN_PROJECT"] = f"{base}-{suffix}" # 프로젝트명을 bunpil에서 bunpil-prod / bunpil-dev 로 나누어서 LangSmith에서 구분할 수 있음. 
