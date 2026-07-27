"""출제 문항 세트의 구조 유사도를 채점하는 로직 — 런타임(graph.py의 judge_node)과
오프라인 eval(evals/eval_lib.py의 judge_structure_one)이 이 STRUCTURE_JUDGE_TPL·
judge_structure()를 공유한다.

2026-07-23 도입 배경: 이전엔 생성 에이전트 자신이 similarity_judge 도구로 자기
출력을 스스로 채점했다(self-judge) — 그런데 이 self-judge 신뢰도는 사람 라벨과
한 번도 대조된 적이 없었고, 검증(오프라인 eval)에 쓰는 Judge 모델과 실제 배포된
Judge가 서로 다른 코드 경로였다(검증-배포 불일치). 이를 해소하기 위해 생성 모델과
Judge 모델을 완전히 분리하고, 런타임도 오프라인 eval과 동일한 함수를 그대로
호출하도록 통합했다 — 이제 EVAL.md의 구조 Judge 신뢰도 수치가 실제 배포된
judge를 그대로 측정한 값이 된다.
"""
import asyncio
import concurrent.futures
import json

from app.common.llm import PromptTemplate

# overall_score 채점 기준 — structure_golden.json의 _schema.overall_score_rubric과 반드시 동기 유지.
# 2026-07-11 첫 정식 측정에서 사람 라벨은 이 rubric(중복·복사·주제 이탈 감점)을 따르는데
# Judge 프롬프트는 유형·난이도 구조만 물어봐 overall κ가 -0.103까지 무너지는 미정렬을 확인,
# rubric을 프롬프트에 주입함(EVAL.md 5절 참고).
STRUCTURE_JUDGE_TPL = PromptTemplate(
    system=(
        "예시 문제와 새로 생성된 문항 세트를 비교해 평가하세요. "
        "문항 개수 일치 여부는 판단하지 마세요 — 개수는 별도로 코드가 검증합니다.\n"
        "다음 3가지를 JSON으로만 응답하세요.\n"
        "- type_ratio_score(유형 구성 비율 유사도, 0.0~1.0)\n"
        "- difficulty_match(난이도 구성 부합, true/false)\n"
        "- overall_score(0~5 정수): 단순한 유형·난이도 일치가 아니라, 예시의 주제·형식을 "
        "유지하면서 '새로운' 문항 세트로 변환했는지를 종합 평가합니다. "
        "예시 문제를 그대로 복사한 경우, 세트 안에 같은 문항이 반복되는 경우(문장이 완전히 "
        "동일하지 않아도 표현만 바꿔 사실상 같은 것을 묻는 패러프레이즈 반복도 포함— "
        "예: '다음 중 A인 것은?'과 '다음은 A를 의미하는가?'처럼 형식만 다를 뿐 같은 질문), "
        "주제가 이탈한 경우, 교육과정에 없는 개념을 지어낸 경우(환각), "
        "한국어가 아닌 텍스트가 섞인 경우는 반드시 감점하세요. "
        "**단, 같은 주제·개념 범주 안에서도 서로 다른 지점(정의, 사례 적용, 원인, 결과, "
        "비교 등)을 묻는 문항들은 표현이 비슷해 보여도 반복이 아니므로 감점하지 마세요. "
        "실질적으로 같은 것을 묻는 경우에만 반복으로 간주하세요.**\n"
        "  5: 유형·난이도·주제·형식이 매우 잘 맞고, 새 문항으로 충분히 변형되며 중복·심각한 오류 없음\n"
        "  4: 전반적으로 잘 맞으나 경미한 반복, 표현 오류, 일부 내용 결함\n"
        "  3: 핵심 구조는 유지하지만 뚜렷한 중복, 오류, 환각, 일부 유형·주제 손상\n"
        "  2: 일부 구조만 재현하며 유형 누락, 큰 주제 이탈, 심한 품질 저하\n"
        "  1: 원문 단순 복사 또는 완전 중복에 의존해 새 문항 생성으로 보기 어려움(형식 일치는 최소한 있음)\n"
        "  0: 유형 완전 반전, 언어 오염, 구조 붕괴 등으로 사실상 사용 불가\n"
        "판단이 애매하다고 해서 무조건 3점으로 두지 마세요. 3점도 다른 점수와 마찬가지로 "
        "명확한 근거가 있을 때만 주는 점수입니다 — 각 점수 정의를 다시 검토해 가장 부합하는 "
        "점수를 선택하세요.\n"
        '형식: {"type_ratio_score": 실수, "difficulty_match": true/false, "overall_score": 정수}'
    ),
    few_shots=[
        {
            "user": '{"예시_문제": "1. 시장 실패의 원인은?(객관식)", "생성된_세트": [{"question":"공공재의 특성으로 옳은 것은?","item_type":"객관식","difficulty":"중"}]}',
            "assistant": '{"type_ratio_score": 1.0, "difficulty_match": true, "overall_score": 5}',
        },
        {
            # 유형·난이도는 일치하지만 세트 내부가 완전 중복 → 구조 점수와 무관하게 낮은 overall
            "user": '{"예시_문제": "1. 기본권 중 자유권은?(객관식)", "생성된_세트": [{"question":"기본권 중 자유권에 해당하는 것은?","item_type":"객관식","difficulty":"중"},{"question":"기본권 중 자유권에 해당하는 것은?","item_type":"객관식","difficulty":"중"},{"question":"기본권 중 자유권에 해당하는 것은?","item_type":"객관식","difficulty":"중"}]}',
            "assistant": '{"type_ratio_score": 1.0, "difficulty_match": true, "overall_score": 1}',
        },
        {
            "user": '{"예시_문제": "1. 선거 원칙?(객관식2+서술형1)", "생성된_세트": [{"question":"보통 선거의 의미는?","item_type":"객관식","difficulty":"하"}]}',
            "assistant": '{"type_ratio_score": 0.5, "difficulty_match": false, "overall_score": 2}',
        },
        {
            # 문장이 다르지만 사실상 같은 질문(패러프레이즈 반복) — 텍스트 유사도로는 안 잡히지만
            # 감점 대상. 2026-07-12: Judge가 이 유형을 놓쳐 overall을 과대평가하는 것이 확인됨
            # (str_048류 사례, EVAL.md 5절 참고).
            "user": '{"예시_문제": "1. 소비자의 기본 권리로 옳은 것은?(객관식)", "생성된_세트": [{"question":"소비자의 기본 권리에 해당하는 것은?","item_type":"객관식","difficulty":"중"},{"question":"소비자가 갖는 권리로 옳은 것은?","item_type":"객관식","difficulty":"중"},{"question":"다음 중 소비자 권리에 해당하는 것은 무엇인가?","item_type":"객관식","difficulty":"중"}]}',
            "assistant": '{"type_ratio_score": 1.0, "difficulty_match": true, "overall_score": 1}',
        },
        {
            # 3점 앵커(2026-07-12 추가): 기존 few-shot 점수 분포가 {1,1,2,4,5}로 3점이
            # 비어있어, 애매한 사례(특히 유의어 치환 반복류, str_010/047 참고)를 만나면
            # Judge가 판단을 회피하듯 3점으로 수렴하는 경향이 확인됨(n=45 재측정, EVAL.md
            # 5절). 세트 절반은 유의어 치환 반복(문항1·2), 나머지 절반은 서로 다른 지점을
            # 묻는 정상 문항(문항3·4)인 "부분적 결함" 사례로 3점을 명확히 앵커링.
            "user": '{"예시_문제": "1. 지방분권이 필요한 이유로 가장 적절한 것은?(객관식)", "생성된_세트": [{"question":"지방분권이 필요한 배경으로 가장 적절한 것은?","item_type":"객관식","difficulty":"중"},{"question":"지방분권이 요구되는 이유 중 가장 적절한 것은?","item_type":"객관식","difficulty":"중"},{"question":"지방분권 실시 이후 나타날 수 있는 부작용으로 옳은 것은?","item_type":"객관식","difficulty":"중"},{"question":"지방분권과 중앙집권의 균형을 맞추기 위한 제도적 장치를 서술하시오.","item_type":"서술형","difficulty":"중"}]}',
            "assistant": '{"type_ratio_score": 0.5, "difficulty_match": true, "overall_score": 3}',
        },
        {
            # 균형 예시: 같은 주제(선거)라도 서로 다른 지점(원칙 구분, 제도 비교, 사례 적용)을
            # 물어 실질적으로 다른 문항 — 표현이 비슷해 보여도 반복으로 감점하면 안 됨.
            # 2026-07-12: 패러프레이즈 반복 few-shot만 넣었더니 Judge가 과도하게 엄격해져
            # (단일 문항조차 감점) 상관관계가 무너진 것을 확인, 균형 문구+예시로 보완.
            "user": '{"예시_문제": "1. 민주 선거의 기본 원칙은?(객관식)", "생성된_세트": [{"question":"보통 선거 원칙의 의미로 옳은 것은?","item_type":"객관식","difficulty":"중"},{"question":"평등 선거와 보통 선거 원칙의 차이를 서술하시오.","item_type":"서술형","difficulty":"중"}]}',
            "assistant": '{"type_ratio_score": 0.5, "difficulty_match": true, "overall_score": 4}',
        },
    ],
)


def _run_async(coro):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=300)


def judge_structure(passage_text: str, items: list, llm) -> dict:
    """passage_text·items(문항 세트, get_draft_items() 또는 골든셋 generated_items 형태)를
    llm(LLMBackend — get_judge_backend() 또는 get_llm_backend())으로 채점한다.
    llm.generate()는 비동기 인터페이스라 동기 호출부(graph.py judge_node 등)를 위해
    _run_async()로 감싼다. 호출 실패는 그대로 예외로 전파된다(fail-fast) — 조용히
    폴백하면 신뢰도가 검증되지 않은 채로 프로덕션 게이트를 통과시키는 문제가 재발한다."""
    content = json.dumps(
        {"예시_문제": passage_text, "생성된_세트": items},
        ensure_ascii=False,
    )
    messages = STRUCTURE_JUDGE_TPL.build(content)
    raw = _run_async(llm.generate(messages))
    try:
        s, e = raw.find("{"), raw.rfind("}") + 1
        scores = json.loads(raw[s:e]) if s >= 0 and e > s else {}
    except Exception:
        scores = {}
    return {
        "type_ratio_score": float(scores.get("type_ratio_score", 0.0)),
        "difficulty_match": bool(scores.get("difficulty_match", False)),
        "overall_score": int(scores.get("overall_score", 0)),
    }
