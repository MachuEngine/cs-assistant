from typing_extensions import TypedDict

# 사용자 입력 사양 - num_items는 사용자 입력을 별도로 받지 않지만 이후 로직에 사용되므로 현상태 유지로 결정
class ExamSpec(TypedDict):
    passage_text: str        # 교사가 붙여넣은 예시 문제 원문 (에이전트 프롬프트에 직접 삽입)
    num_items: int           # 생성할 문항 개수. 예시 문제 개수와 무관 — 기본값 2(main.py가 채움)

# 생성된 개별 문항
class DraftItem(TypedDict):
    item_id: str
    question: str
    options: list            # 객관식 선지. 서술형은 []
    answer: str
    item_type: str           # "객관식" | "서술형"
    difficulty: str          # "상" | "중" | "하"
    standard: str
    judge_score: float       # 0–5, LLM Judge
    status: str              # "approved" | "rejected"

# 그래프 전체 상태
class ExamState(TypedDict):
    spec: ExamSpec           # 사용자 입력 정보 ExamSpec 타입
    draft_items: list        # 누적 문항 (validate 노드가 교체)
    budget: int              # 남은 재시도 횟수
    agent_messages: list     # 에이전트 메시지 (agent 노드가 교체)
    validation_passed: bool
    validation_feedback: str  # 재시도 시 agent가 실제 문항을 교정하도록 전달하는 실패 사유
    similarity_judge_result: dict  # {"type_ratio_score": float, "difficulty_match": bool, "overall_score": int} — count_match는 코드가 spec["num_items"]로 별도 검증(validate_node)
