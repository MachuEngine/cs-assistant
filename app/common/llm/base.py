from abc import ABC, abstractmethod


class LLMBackend(ABC):
    """LangChain 도구 바인딩이 필요 없는 단순 생성 인터페이스.

    Judge(app/modules/reply/judge.py)가 이 인터페이스로 호출된다 — 런타임과
    오프라인 eval이 동일한 함수를 통해 동일한 백엔드를 호출해야 검증-배포
    불일치가 생기지 않는다(DESIGN.md 3.4절).
    """

    @abstractmethod
    async def generate(self, messages: list[dict], **kwargs) -> str:
        """messages: [{"role": "system"|"user"|"assistant", "content": "..."}]"""
        ...
