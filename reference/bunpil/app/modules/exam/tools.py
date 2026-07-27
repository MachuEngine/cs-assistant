import contextvars
import logging
import re
import uuid

logger = logging.getLogger(__name__)

from langchain_core.tools import tool

from app.common.rag import get_retriever, get_store

# ── 세션 컨텍스트 ──
# _request_ctx: 요청별 독립 dict. asyncio.to_thread + contextvars.copy_context()로
# 요청 간 격리 보장. 같은 요청의 worker 스레드들은 동일 dict 객체를 공유하므로
# intra-request 가시성 유지 (GIL로 단순 list/dict 연산은 안전).
_request_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("_request_ctx")

_HANGUL_RE = re.compile(r"[가-힣]")
_HAN_RE = re.compile(r"[一-鿿]")  # CJK 한자 (중국어 오염 검출용)


def _get_ctx() -> dict:
    return _request_ctx.get()


def init_session(passage_text: str = "", target_num: int = 0) -> None:
    # LangGraph는 각 노드를 context.run()으로 격리 실행하므로
    # plan_node 내에서 set()한 새 dict가 agent_node에 전파되지 않는다.
    # 해결: asyncio.to_thread 호출 전 main.py에서 먼저 set()으로 dict를 생성하고,
    # 이후 호출(plan_node)에서는 같은 dict를 in-place로 초기화해 모든 노드가 공유한다.
    # passage_text: save_item의 원문 복사 게이트가 참조 (plan_node가 spec에서 전달)
    try:
        ctx = _request_ctx.get()
        ctx["items"] = []
        ctx["scores"] = {}
        ctx["passage_text"] = passage_text
        ctx["target_num"] = target_num
    except LookupError:
        _request_ctx.set({
            "items": [],
            "scores": {},
            "passage_text": passage_text,
            "target_num": target_num,
        })

"""
**item : dict 언패킹. 
item은 save_item이 저장해둔 문항 하나. 

item = {
    "item_id": "a1b2c3",
    "question": "...",
    "options": [...],
    "answer": "①",
    "item_type": "객관식",
    "difficulty": "중",
    "standard": "",
}

{**item, ...} 은 item 안에 든 모든 키-값 쌍을 새 dict에 그대로 펼쳐 넣어라 라는 의미

eg. 
{**item, "judge_score": score, "status": "..."} 를 풀어 쓰면 아래와 같은 의미.

{
    "item_id": item["item_id"],
    "question": item["question"],
    "options": item["options"],
    "answer": item["answer"],
    "item_type": item["item_type"],
    "difficulty": item["difficulty"],
    "standard": item["standard"],
    "judge_score": score,        # ← item에는 없던 새 필드 추가
    "status": "approved" or "rejected",  # ← 역시 새 필드 추가
}
"""

def get_draft_items() -> list:
    ctx = _get_ctx() # _request_ctx.get : _request_ctx가 가리키는 컨텍스트 (dict)을 가져옴 
    result = []
    for item in ctx["items"]:
        iid = item.get("item_id", "")
        score = ctx["scores"].get(iid, 0.0)
        result.append(
            {
                **item,
                "judge_score": score,
                "status": "approved" if score >= 3 else "rejected",
            }
        )
    return result

"""
context (dict)
    {
        "items": list,          # 문항 dict들의 리스트
        "scores": dict,         # item_id(str) → score(float) 매핑
        "passage_text": str,    # 교사가 입력한 예시 문제 원문
        "target_num": int,      # 목표 문항 개수 (예: 5)
    }
"""


# ── 도구 정의 ──
# 모든 도구는 LLM 호출 없이 순수 계산/검색/저장만 수행한다.
# 추론과 생성은 에이전트(LLM)가 직접 담당한다.

@tool
def search_regulations(query: str) -> str:
    """교육과정 법령·지침에서 관련 내용을 검색합니다. query: 검색 키워드"""
    count = get_store().count("regulations") # regulations 컬렉션의 청크 개수를 반환 
    if count == 0:
        logger.warning("regulations 컬렉션이 비어있습니다.")
        return "교육과정 자료 없음"
    results = get_retriever().retrieve(query, "regulations", top_k=3)
    if not results:
        return "관련 규정 없음"
    return "\n\n".join(f"[{i+1}] {r['text'][:300]}" for i, r in enumerate(results))
"""
(return 되는 f-string 포맷 예시)
    "[1] 2022 개정 교육과정 총론에 따르면 사회과 평가는...
     [2] 성취기준 서술 시 유의사항은 다음과 같다...
     [3] 문항 출제 시 특정 종교·정치적 견해를...
     ..."
위와 같은 문자열 전체가 LLM에게 도구 실행 결과로 전달 됨.
"""

@tool
def search_standards(query: str) -> str:
    """성취기준 관련 내용을 사회과 교육과정(2022 개정) standards 컬렉션에서 검색합니다.
    query: 검색 키워드 (예: 성취기준명)"""
    count = get_store().count("standards")
    if count == 0:
        logger.warning("standards 컬렉션이 비어있습니다.")
        return "교육과정 성취기준 자료 없음"
    results = get_retriever().retrieve(query, "standards", top_k=3)
    if not results:
        return "관련 성취기준 없음"
    return "\n\n".join(f"[{i+1}] {r['text'][:400]}" for i, r in enumerate(results))


@tool
def validate_item_format(question: str, options: list, answer: str, item_type: str) -> str:
    """문항 형식을 검증합니다. 오류가 있으면 구체적인 수정 지침을 반환합니다.
    question: 문제 질문
    options: 선지 목록 (객관식: ["①...", "②...", "③...", "④..."], 서술형: [])
    answer: 정답 (객관식: "①"~"④", 서술형: "")
    item_type: 객관식|서술형
    """
    errors = _format_errors(question, options, answer, item_type)
    if errors:
        return "형식 오류 — 수정 필요: " + " / ".join(errors)
    return "형식 검증 통과"


def _format_errors(question: str, options: list, answer: str, item_type: str) -> list[str]:
    """validate/save가 함께 사용하는 결정론적 형식 검증."""
    errors = []
    if not question or len(question.strip()) < 10:
        errors.append("질문이 너무 짧습니다 (10자 이상 필요)")
    if item_type not in ("객관식", "서술형"):
        errors.append(f"문항 유형은 객관식 또는 서술형이어야 합니다 (현재: '{item_type}')")
    elif item_type == "객관식":
        if len(options) != 4:
            errors.append(f"선지는 4개여야 합니다 (현재 {len(options)}개)")
        marks = ["①", "②", "③", "④"]
        if answer not in marks:
            errors.append(f"정답은 ①②③④ 중 하나여야 합니다 (현재: '{answer}')")
        for i, opt in enumerate(options[:4]):
            if not str(opt).startswith(marks[i]):
                errors.append(f"선지 {i+1}번이 '{marks[i]}'로 시작해야 합니다")
                break
    elif options:
        errors.append("서술형 문항의 options는 빈 목록이어야 합니다")
    return errors


def _check_korean(question: str, options: list, answer: str) -> str | None:
    """문항 텍스트가 한국어인지 결정론적으로 검사한다. 통과하면 None, 아니면 거부 사유 반환.

    qwen2.5:7b가 컨텍스트가 길어지면 확률적으로 중국어 문항을 생성하는 문제가 있어
    (2026-07-11 발견, TROUBLESHOOTING.md 참고) 저장 전에 코드가 차단한다.
    한자 비율 5% 미만은 허용 — 정당한 괄호 병기(예: 사법(私法))까지 막지 않기 위함."""
    text = " ".join([str(question), str(answer), *[str(o) for o in options]])
    hangul = len(_HANGUL_RE.findall(text))
    han = len(_HAN_RE.findall(text))
    if hangul == 0:
        return "저장 거부 — 문항에 한국어가 없습니다. 모든 내용을 한국어로 작성한 뒤 다시 저장하세요."
    if han and han / (han + hangul) >= 0.05:
        return "저장 거부 — 문항에 중국어가 포함되어 있습니다. question·options·answer 전체를 한국어로 다시 작성한 뒤 저장하세요."
    return None


# ── 유사도 게이트 ──
# qwen2.5:7b가 예시 문제를 그대로 복사하거나 세트 안에 같은 문항을 반복 생성하는 문제
# (2026-07-11, 사람 라벨 20건 중 최다 감점 사유)의 결정론적 차단. 7B Judge는 rubric을
# 줘도 텍스트 동일성 대조를 못 해내서(EVAL.md 5절) 코드로 이관함.
# 임계값 근거: 라벨링된 골든셋 실측 분포 — 완전 복사는 containment 1.00, 정상적인 주제
# 유사 변형은 ~0.73 이하 / 진짜 중복은 jaccard 0.86~1.00, 정상 변형은 ~0.67 이하.
_SIMILARITY_STRIP_RE = re.compile(r"[\s\d①②③④.,?!()\[\]·:;'\"—\-~%]")
_PASSAGE_COPY_THRESHOLD = 0.90
_DUPLICATE_JACCARD_THRESHOLD = 0.80


def _bigrams(text: str) -> set:
    t = _SIMILARITY_STRIP_RE.sub("", str(text))
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _check_similarity(question: str) -> str | None:
    """예시 문제 원문 복사·세트 내 중복을 검사한다. 통과하면 None, 아니면 거부 사유 반환."""
    qb = _bigrams(question)
    if len(qb) < 8:  # 극단적으로 짧은 질문은 판정 불가 — 길이 검증은 validate_item_format 몫
        return None
    ctx = _get_ctx()
    passage = ctx.get("passage_text", "")
    if passage:
        pb = _bigrams(passage)
        if pb and len(qb & pb) / len(qb) >= _PASSAGE_COPY_THRESHOLD:
            return ("저장 거부 — 이 문항은 예시 문제를 거의 그대로 복사한 것입니다. "
                    "예시는 참고만 하고, 같은 주제라도 질문·선지를 새로 구성해 다시 저장하세요.")
    for existing in ctx["items"]:
        eb = _bigrams(existing.get("question", ""))
        union = qb | eb
        if union and len(qb & eb) / len(union) >= _DUPLICATE_JACCARD_THRESHOLD:
            return ("저장 거부 — 이미 저장된 문항과 사실상 동일합니다. "
                    "다른 개념이나 다른 관점을 묻는 새 문항을 작성해 저장하세요.")
    return None


@tool
def save_item(question: str, options: list, answer: str, item_type: str, difficulty: str = "중", standard: str = "") -> str:
    """검증된 문항을 저장합니다. 에이전트가 직접 작성한 내용을 저장합니다.
    (다음 문항은 저장이 거부됩니다 — 한국어가 아닌 문항, 예시 문제를 그대로 복사한 문항,
    이미 저장된 문항과 동일한 문항. 거부 시 안내에 따라 새로 작성해 재시도하세요.)
    question: 문제 질문
    options: 선지 목록 (객관식: ["①...", "②...", "③...", "④..."], 서술형: [])
    answer: 정답 (객관식: "①"~"④", 서술형: "")
    item_type: 객관식|서술형
    difficulty: 상|중|하
    standard: 성취기준명 (선택)
    """
    format_errors = _format_errors(question, options, answer, item_type)
    if difficulty not in ("상", "중", "하"):
        format_errors.append(f"난이도는 상·중·하 중 하나여야 합니다 (현재: '{difficulty}')")
    if format_errors:
        return "저장 거부 — 형식 오류: " + " / ".join(format_errors)

    ctx = _get_ctx()
    target_num = ctx.get("target_num", 0)
    if target_num and len(ctx["items"]) >= target_num:
        return (
            f"저장 거부 — 목표 문항 수({target_num}개)를 이미 채웠습니다. "
            "교체가 필요하면 discard_item으로 기존 문항을 먼저 폐기하세요."
        )

    rejection = _check_korean(question, options, answer) or _check_similarity(question)
    if rejection:
        return rejection
    item_id = uuid.uuid4().hex[:8]
    item = {
        "item_id": item_id,
        "question": question,
        "options": options,
        "answer": answer,
        "item_type": item_type,
        "difficulty": difficulty,
        "standard": standard,
    }
    ctx["items"].append(item)
    return f"저장 완료 (item_id={item_id}). 다음 턴에 이 item_id로 record_score를 호출하세요."


@tool
def record_score(item_id: str, score: int) -> str:
    """문항 품질 점수를 기록합니다. 에이전트가 직접 평가한 점수를 입력합니다.
    item_id: save_item이 반환한 문항 ID
    score: 0~5 (5=매우 우수, 4=우수, 3=보통, 2=미흡, 1=불량, 0=생성 실패)
    """
    ctx = _get_ctx()
    if not any(item.get("item_id") == item_id for item in ctx["items"]):
        return f"점수 기록 거부 — 존재하지 않는 item_id: {item_id}"
    ctx["scores"][item_id] = float(max(0, min(5, int(score))))
    return f"품질 점수 {score}/5 기록됨"


@tool
def discard_item(item_id: str) -> str:
    """검증에 실패한 기존 문항을 폐기합니다. 교체할 문항의 item_id를 입력하세요."""
    ctx = _get_ctx()
    for index, item in enumerate(ctx["items"]):
        if item.get("item_id") == item_id:
            ctx["items"].pop(index)
            ctx["scores"].pop(item_id, None)
            return f"문항 폐기 완료 (item_id={item_id})"
    return f"문항 폐기 거부 — 존재하지 않는 item_id: {item_id}"


@tool
def submit_for_review() -> str:
    """문항 세트 작성을 모두 마쳤다는 신호입니다. 문항 저장(save_item)과 채점
    (record_score)이 끝난 뒤 이 도구를 호출하세요. 구조 유사도 평가·문항 개수
    검증은 이 도구가 아니라 시스템이 자동으로 수행합니다.

    2026-07-23: 이전엔 생성 에이전트가 similarity_judge 도구로 자기 출력을 스스로
    채점했으나(self-judge), 사람 라벨 대비 신뢰도가 검증된 적이 없어 별도 Judge
    노드(judge_node, get_judge_backend())로 분리했다 — 이 도구는 그 채점 이전에
    "작성이 끝났다"는 신호만 전달한다."""
    return "제출 완료 — 구조 유사도 평가를 진행합니다."


TOOLS = [
    search_regulations,
    search_standards,
    validate_item_format,
    save_item,
    record_score,
    discard_item,
    submit_for_review,
]
