from abc import ABC, abstractmethod


class LLMBackend(ABC):
    @abstractmethod
    async def generate(self, messages: list[dict], **kwargs) -> str:
        """messages: [{"role": "system"|"user"|"assistant", "content": "..."}]"""
        ...
