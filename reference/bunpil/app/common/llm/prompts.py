from dataclasses import dataclass, field


@dataclass
class PromptTemplate:
    """Few-shot + CoT 프롬프트 빌더. 모듈별로 인스턴스를 생성해 주입한다."""

    system: str
    few_shots: list[dict] = field(default_factory=list) 
    # few_shots 형식: [{"user": "...", "assistant": "..."}]

    # 기본 list를 사용하지 않는 이유 -> 
    # =[]를 그대로 쓰면 모든 인스턴스가 같은 리스트를 공유하게 됨. 
    # default_factory=list는 인스턴스가 생성될 때 마다 list:()를 새로 호출해서
    # 인스턴스별 독립된 리스트를 보장함

    cot_prefix: str = ""
    # cot_prefix가 있으면 assistant 턴 앞에 붙여 CoT를 유도한다.
    # -> cot_prefix는 CoT(Chain-of-Thought)를 유도하기 위해 assistant 턴 맨 앞에 미리 깔아두는 문장
    # CoT(사고의 연쇄)는 모델이 답을 바로 내지 않고, 단계별로 풀어가며 생각하게 유도하는 프롬프팅 기법
    # 예를 들어 cot_prefix="단계적으로 생각해 보겠습니다.\n"라고 설정했다면,
    # LLM API에 메시지를 보낼 때, 마지막 턴이 assistant면 모델은 "이 문장을 이어서 완성해라"고 이해함
    # 그럼 모델이 자연스럽게 아래와 같이 진행됨
    # 단계적으로 생각해 보겠습니다.
    # 1. 사회계약론은 정부의 정당성을 개인 간 계약에서 찾는 이론이다.
    # 2. 대표적 사상가로는 홉스, 로크, 루소가 있다.
    # ...

    def build(self, user_input: str) -> list[dict]:
        messages = [{"role": "system", "content": self.system}]
        for shot in self.few_shots:
            messages.append({"role": "user", "content": shot["user"]})
            messages.append({"role": "assistant", "content": shot["assistant"]})
        messages.append({"role": "user", "content": user_input})
        if self.cot_prefix:
            messages.append({"role": "assistant", "content": self.cot_prefix})
        return messages


"""
- SYSTEM PROMPT 
- FEW SHOT 
- USER INPUT
- COT PREFIX 

messages = [
    {"role": "system", "content": self.system},

    {"role": "user", "content": few_shots[0]["user"]},
    {"role": "assistant", "content": few_shots[0]["assistant"]},

    {"role": "user", "content": few_shots[1]["user"]},        # few_shots 개수만큼 반복
    {"role": "assistant", "content": few_shots[1]["assistant"]},

    {"role": "user", "content": user_input},                   

    {"role": "assistant", "content": self.cot_prefix},         # cot_prefix 있을 때만
]
"""