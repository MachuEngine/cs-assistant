import logging
import re
import time
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from app.common.llm import get_judge_backend

from .judge import judge_structure
from .llm import get_langchain_model
from .state import ExamState
from .tools import TOOLS, get_draft_items, init_session

logger = logging.getLogger(__name__)


def _invoke_with_retry(llm, messages, retries: int = 2, delay: float = 2.0):
    """장시간 세션에서 간헐적으로 발생하는 Ollama 스트림 오류
    ("No data received from Ollama stream")를 흡수한다. 2026-07-10 발견 —
    TROUBLESHOOTING.md 참고. 프롬프트/모델 문제가 아니라 연결 자체의 일시적 오류라
    같은 요청을 그대로 재시도하면 대부분 해결됨."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            return llm.invoke(messages)
        except Exception as e:
            last_err = e
            logger.warning("LLM invoke 실패(%d/%d), 재시도: %s", attempt + 1, retries + 1, e)
            if attempt < retries:
                time.sleep(delay)
    raise last_err


# tool_calls가 비었을 때, 모델이 정말 자발적으로 끝낸 것인지 도구 호출을 시도했으나
# 형식이 깨진 것인지 구분하는 휴리스틱. 2026-07-11 발견 — qwen2.5:7b/14b 둘 다 재현됨
# (TROUBLESHOOTING.md 참고): content에 `{"name": ...}` 형태의 JSON이나 `<tool_call>`류
# 태그 흔적이 있으면 도구를 부르려던 시도로 간주한다.
_BROKEN_TOOL_CALL_RE = re.compile(r'"name"\s*:\s*"|</?tool_call>', re.IGNORECASE)


def _looks_like_broken_tool_call(content) -> bool:
    return bool(_BROKEN_TOOL_CALL_RE.search(str(content or "")))


def plan_node(state: ExamState) -> dict:
    """세션을 초기화한다. 요청 전체에서 단 한 번만 호출됨 — 재시도 시에는 agent_node가
    이미 저장된 문항을 유지한 채 부족분만 이어서 작성한다(부분 진행을 재시도마다 버리지
    않기 위함, 2026-07-10 개선. 이전에는 agent_node가 매 재시도마다 init_session()으로
    전체 초기화를 했었음). passage_text는 save_item의 원문 복사 게이트가 참조."""
    init_session(
        state["spec"].get("passage_text", ""),
        state["spec"].get("num_items", 2),
    )
    return {
        "validation_passed": False,
        "similarity_judge_result": {},
        "validation_feedback": "",
    }


def _build_system_prompt(
    passage_text: str,
    num_items: int,
    existing_items: list,
    validation_feedback: str = "",
) -> str:
    no_text_rule = (
        "**매우 중요한 규칙**: 이 대화 내내 도구 호출(tool call) 외에는 어떤 텍스트도 "
        "출력하지 마세요. 인사, 생각 과정 설명, 진행 상황 서술, 문항 초안을 텍스트로 "
        "먼저 보여주는 것 모두 금지입니다. 매 턴 오직 도구 호출만 하세요."
    )
    remaining = max(0, num_items - len(existing_items))

    def _summary(items):
        """
        기존 문항들을 사람이 읽을 만한 요약 목록으로 변환하는 내부 헬퍼. 
        item_id, 유형/난이도, 점수, 질문 앞 40자만 보여줌 — 전체 문항 텍스트를
        다 넣으면 프롬프트가 불필요하게 길어지니 식별에 필요한 만큼만.
        """
        return "\n".join(
            f"  {i+1}. [id={it.get('item_id','?')}, {it.get('item_type','?')}/{it.get('difficulty','?')}, "
            f"score={it.get('judge_score', 0)}] {str(it.get('question',''))[:40]}"
            for i, it in enumerate(items)
        )

    if not existing_items:
        progress_note = ""
        count_instruction = (
            "예시 문제는 스타일·주제·난이도 참고용입니다. 문항 개수는 예시 개수와 무관하게 "
            f"지정된 개수({num_items}개)에 맞춰 작성하세요. 유형(객관식/서술형) 구성과 난이도 수준은 "
            "예시를 참고해 구성하되, 개수만은 반드시 지정된 개수를 따르세요.\n\n"
            "**예시 문제의 질문·선지 문구를 그대로 또는 거의 그대로 재사용하면 저장이 거부됩니다.** "
            "같은 주제라도 묻는 지점(개념 정의, 사례 적용, 비교, 원인/결과 등)을 바꿔 완전히 새로운 "
            "질문과 선지를 작성하세요. 세트 안에서 같은 문항을 반복해도 저장이 거부됩니다."
        )
        action_instruction = "문항마다 다음 순서로 도구를 호출하세요:"
    elif remaining > 0:
        progress_note = (
            f"\n\n이전 시도에서 이미 다음 {len(existing_items)}개 문항을 작성해 저장했습니다"
            f"(다시 만들지 마세요, 그대로 유지됩니다):\n{_summary(existing_items)}\n"
        )
        count_instruction = (
            f"목표 개수는 {num_items}개이고 이미 {len(existing_items)}개가 있으므로, "
            f"나머지 {remaining}개만 새로 작성하세요. 기존 문항과 겹치지 않는 내용으로, "
            "예시 문제 스타일·난이도를 참고해 구성하세요."
        )
        action_instruction = f"나머지 {remaining}개 문항마다 다음 순서로 도구를 호출하세요:"
    elif remaining < 0:
        progress_note = f"\n\n목표보다 많은 {len(existing_items)}개 문항이 저장돼 있습니다:\n{_summary(existing_items)}\n"
        count_instruction = (
            f"목표는 {num_items}개입니다. discard_item으로 초과 문항 {abs(remaining)}개를 폐기한 뒤 "
            "남은 세트를 submit_for_review로 제출하세요. 새 문항을 추가하지 마세요."
        )
        action_instruction = "초과 문항을 폐기하고 submit_for_review를 호출하세요:"
    else:
        progress_note = f"\n\n목표 개수({num_items}개)는 이미 채워져 있습니다:\n{_summary(existing_items)}\n"
        if validation_feedback:
            count_instruction = (
                f"이전 검증 실패 사유: {validation_feedback}\n"
                "단순 재제출만 하지 마세요. 실패 사유에 해당하는 문항을 discard_item으로 폐기하고, "
                "유형·난이도·품질을 교정한 새 문항을 저장·채점한 뒤 submit_for_review를 다시 호출하세요."
            )
            action_instruction = "문항을 실제로 교체한 뒤 submit_for_review를 호출하세요:"
        else:
            count_instruction = "새 문항을 추가하지 말고 submit_for_review로 제출하세요."
            action_instruction = "바로 submit_for_review 도구를 호출하세요:"

    return (
        "당신은 한국 고등학교 사회 문항 출제 전문가 에이전트입니다. 한국어로만 응답하세요.\n\n"
        f"{no_text_rule}\n\n"
        "다음은 교사가 참고용으로 제시한 예시 문제입니다.\n\n"
        f"[예시 문제]\n{passage_text}\n"
        f"{progress_note}\n"
        f"{count_instruction}\n\n"
        f"{action_instruction}\n"
        "1. [가능하면] search_standards — 문항 주제에 맞는 성취기준을 검색해 확인하세요. "
        "관련 자료가 없다고 나오면 성취기준 없이 진행하세요\n"
        "2. [선택] search_regulations — 교육과정 준수 사항 확인\n"
        "3. validate_item_format — 직접 구성한 문항의 형식 검증\n"
        "   (오류가 있으면 수정 후 재검증, 통과할 때까지 반복)\n"
        "4. save_item — 검증 통과한 문항 저장\n"
        "5. save_item 응답을 받은 다음 턴에 record_score — 반환된 item_id와 품질 점수(0~5점) 기록\n"
        "6. [교체 시] discard_item — 기존 문항을 폐기한 뒤 새 문항 저장\n\n"
        "문항 세트 작성이 모두 끝나면 submit_for_review 도구를 호출해 제출하세요. "
        "(구조 유사도 평가·문항 개수 검증은 이 도구가 아니라 시스템이 자동으로 수행합니다.)\n\n"
        "문항은 당신이 직접 작성합니다. "
        "객관식 선지는 반드시 ①②③④ 형식으로 4개 작성하세요.\n\n"
        "오답(정답이 아닌 선지)은 명백히 틀리거나 문제와 무관한 내용이 아니라, "
        "같은 개념 범주 안에서 학생이 실제로 헷갈릴 수 있는 그럴듯한 오답으로 구성하세요. "
        "예를 들어 정답이 '비례대표제'라면 오답은 '외계인 침공'처럼 무관한 선지가 아니라 "
        "'소선거구제', '직접 선거제'처럼 같은 주제의 인접 개념이어야 합니다.\n\n"
        f"{no_text_rule}"
    )


def agent_node(state: ExamState) -> dict:
    """ReAct 에이전트가 예시 문제를 분석해 문항 세트를 생성한다.

    2026-07-10 개선: 재시도마다 전체를 초기화하지 않는다. 이미 저장된 문항
    (get_draft_items())은 유지하고, 부족한 개수만 이어서 작성하도록 프롬프트를
    동적으로 구성한다.

    2026-07-23: 구조 유사도 자기채점(similarity_judge)을 제거했다 — 이제 에이전트는
    문항 작성·저장·채점 후 submit_for_review로 "끝났다"는 신호만 보내고, 실제
    구조 유사도 채점은 별도 judge_node(get_judge_backend())가 담당한다.
    """
    spec = state["spec"]
    passage_text = spec.get("passage_text", "")
    num_items = spec.get("num_items", 2)
    existing_items = get_draft_items()

    system_prompt = _build_system_prompt(
        passage_text,
        num_items,
        existing_items,
        state.get("validation_feedback", ""),
    )
    user_content = "위 지침에 따라 문항을 작성하세요."

    tool_map = {t.name: t for t in TOOLS}
    # @tool 데코레이션이 붙은 함수는 그냥 함수가 아닌 tool 객체임.
    # tool 객체는 .name 등의 속성을 가지고 있음
    # tool_map = {
    #   "search_regulations": search_regulations,
    #   "search_standards": search_standards,
    #   "validate_item_format": validate_item_format,
    #   "save_item": save_item,
    #   "record_score": record_score,
    #   "discard_item": discard_item,
    #   "submit_for_review": submit_for_review,
    # }
    llm = get_langchain_model().bind_tools(TOOLS)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_content)]

    # 형식이 깨진 tool_call 응답에 대한 연속 재시도 카운터. 2026-07-11: tool_calls가
    # 비어있으면 무조건 "에이전트가 자발적으로 끝냈다"고 오판해 즉시 루프를 끊었는데,
    # 실제로는 모델이 도구를 부르려다 형식만 깨뜨린 경우가 섞여 있었음(TROUBLESHOOTING.md).
    # 연속 2회까지는 형식 오류로 보고 재작성을 요청하고, 그래도 안 되면 원래대로 종료
    # (바깥쪽 turn cap 14가 항상 최종 방어선 — 무한루프는 불가능).

    # 최대 14턴까지만 루프.
    # malformed_streak: 연속으로 몇 번 "tool_calls는 비었는데 진짜 안 끝난 것 같다"고 판단했는지 세는 카운터
    malformed_streak = 0
    for _ in range(14):
        response = _invoke_with_retry(llm, messages)
        messages.append(response)

        # getattr - 파이썬 내장 함수.
        # 어떤 객체에서 특정 이름(문자열)의 속성을 꺼내오되, 그 속성이 없으면 기본값을 사용해라
        if not getattr(response, "tool_calls", []):
            incomplete = len(get_draft_items()) != num_items
            # 진짜 끝난게 아니고 도구 호출에 이상이 있는 케이스 && 이 도구 호출 안되는 경우가 3번 미만 째인지
            if incomplete and malformed_streak < 3:
                malformed_streak += 1
                reason = (
                    "도구 호출 형식이 손상되었습니다."
                    if _looks_like_broken_tool_call(response.content)
                    else "아직 목표 문항 저장과 제출이 끝나지 않았습니다."
                )
                messages.append(HumanMessage(
                    content=f"{reason} 설명 없이, "
                    "정확한 tool call 형식으로 다시 시도하세요."
                ))
                continue
            break

        malformed_streak = 0
        submitted = False
        for tc in response.tool_calls:
            fn = tool_map.get(tc["name"])
            if not fn:
                result_content = f"Unknown tool: {tc['name']}"
            else:
                try:
                    result_content = str(fn.invoke(tc["args"]))
                except Exception as e:
                    # 소형 LLM이 인자 타입을 틀리는 경우가 있어(예: 리스트 대신 문자열 필드에
                    # 리스트를 채움), 예외로 전체 루프를 죽이지 않고 에이전트가 스스로
                    # 고칠 수 있도록 오류를 도구 응답 형태로 되돌려준다.
                    result_content = f"도구 호출 오류 — 인자 형식을 확인하고 다시 호출하세요: {e}"
            messages.append(ToolMessage(content=result_content, tool_call_id=tc["id"]))
            if tc["name"] == "submit_for_review":
                submitted = True
        if submitted:
            break

    return {
        "agent_messages": messages,
        "budget": state["budget"] - 1,
    }


@traceable(name="judge_node", run_type="chain")
def judge_node(state: ExamState) -> dict:
    """생성된 문항 세트의 구조 유사도를 외부 Judge 백엔드(get_judge_backend())로 채점한다.

    2026-07-23 도입: 이전엔 생성 에이전트 자신이 similarity_judge 도구로 자기 출력을
    스스로 채점했다(self-judge) — 이 self-judge 신뢰도는 사람 라벨과 한 번도 대조된
    적이 없었고, 오프라인 eval이 검증하는 Judge(get_judge_backend())와 실제 배포된
    Judge(생성 모델 자신)가 서로 다른 코드 경로였다(검증-배포 불일치). 이제 런타임도
    오프라인 eval(evals/eval_lib.py judge_structure_one)과 동일한 judge_structure()를
    호출해 두 경로가 항상 같은 judge를 측정하도록 통일했다.

    Judge 호출이 실패하면(예: OPENAI_API_KEY 누락) 그대로 예외를 전파한다(fail-fast) —
    조용히 폴백하면 신뢰도가 검증되지 않은 채로 프로덕션 게이트를 통과시키는 문제가
    재발하므로, 실패를 감추지 않고 명확한 에러로 드러낸다."""
    spec = state["spec"]
    passage_text = spec.get("passage_text", "")
    items = get_draft_items()
    judge_llm = get_judge_backend()
    result = judge_structure(passage_text, items, judge_llm)
    return {"similarity_judge_result": result}


def validate_node(state: ExamState) -> dict:
    """judge_node가 채점한 similarity_judge_result를 threshold로 판정한다.
    count_match는 LLM 판단이 아니라 spec["num_items"] 기준으로 코드가 직접 계산한다
    (문항 개수는 예시 문제 개수와 무관하게 지정된 값을 따라야 하므로)."""
    judge = state.get("similarity_judge_result", {})
    draft_items = get_draft_items()
    count_match = len(draft_items) == state["spec"].get("num_items", 2)
    rejected_ids = [
        item.get("item_id", "") for item in draft_items if item.get("status") != "approved"
    ]
    all_approved = not rejected_ids
    passed = (
        count_match
        and all_approved
        and judge.get("type_ratio_score", 0) >= 0.7
        and judge.get("difficulty_match", False)
        and judge.get("overall_score", 0) >= 4
    )
    feedback = []
    if not count_match:
        feedback.append(
            f"문항 개수 불일치(목표 {state['spec'].get('num_items', 2)}개, 현재 {len(draft_items)}개)"
        )
    if rejected_ids:
        feedback.append(f"품질 점수 미달 또는 미채점 문항: {', '.join(rejected_ids)}")
    if not judge:
        feedback.append("구조 유사도 미채점")
    else:
        if judge.get("type_ratio_score", 0) < 0.7:
            feedback.append("유형 비율 유사도 미달")
        if not judge.get("difficulty_match", False):
            feedback.append("난이도 구성 불일치")
        if judge.get("overall_score", 0) < 4:
            feedback.append("종합 구조 유사도 점수 미달")
    return {
        "draft_items": draft_items,
        "validation_passed": passed,
        "validation_feedback": "; ".join(feedback),
    }


def should_retry(state: ExamState) -> Literal["agent", "end"]:
    if state.get("validation_passed"):
        return "end"
    if state.get("budget", 0) > 0:
        return "agent"
    return "end"


def build_exam_graph():
    g = StateGraph(ExamState)
    g.add_node("plan", plan_node)
    g.add_node("agent", agent_node)
    g.add_node("judge", judge_node)
    g.add_node("validate", validate_node)

    g.add_edge(START, "plan")
    g.add_edge("plan", "agent")
    g.add_edge("agent", "judge")
    g.add_edge("judge", "validate")
    g.add_conditional_edges("validate", should_retry, {"agent": "agent", "end": END})

    return g.compile()


_exam_graph = None


def get_exam_graph():
    global _exam_graph
    if _exam_graph is None:
        _exam_graph = build_exam_graph()
    return _exam_graph
