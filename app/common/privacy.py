"""PII 마스킹 — 모델 호출 이전에 실행해야 한다(DESIGN.md 5절).

마스킹 대상(개인 식별정보): 이메일 · 전화번호 · 신용카드번호(Luhn 검증) ·
우편주소 · 인명 → {{EMAIL}} {{PHONE}} {{CARD}} {{ADDRESS}} {{NAME}}

마스킹 금지(내부 식별자): 주문번호(ORD-…) · 송장번호(INV-…) · 고객ID(CUST-…).
이 함수는 그런 패턴에 관여하지 않는다 — lookup_order 등 도구가 입력으로
써야 하므로 가리면 안 된다.

순수 함수. 모델 호출·I/O 없음 — 단위 테스트만으로 완결된다.
"""
import re

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b")

# 국가번호(선택) + 지역번호(3자리) + 3자리 + 4자리. 구분자는 공백/점/하이픈/괄호.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"
)

# 신용카드 후보: 13~19자리 숫자, 자릿수 사이에 공백/하이픈이 끼어 있어도 된다.
_CARD_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")

# 번지수 + 도로명 + 도로 유형(약어 포함). 예: "123 Main Street", "456 Oak Ave"
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'\s]{2,40}?\b(?:Street|St|Avenue|Ave|Road|Rd|"
    r"Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Place|Pl|Way|Terrace|Ter)\b\.?",
    re.IGNORECASE,
)

# 인명 탐지는 일반 NER 없이 완전히 신뢰할 수 없다 — 이름 가제티어(흔한
# 영문 이름 목록) + "Title Case 두 단어 연속" 구조로 오탐을 억제하는 v1
# 휴리스틱이다. 목록에 없는 이름은 놓칠 수 있음(재현율 < 100%, 알려진 한계).
FIRST_NAMES = frozenset({
    "Olivia", "Liam", "Emma", "Noah", "Ava", "Ethan", "Sophia", "Mason",
    "Isabella", "Lucas", "Mia", "Henry", "Amelia", "Jack", "Harper", "Owen",
    "Evelyn", "Leo", "Charlotte", "Wyatt", "Grace", "Julian", "Chloe", "Levi",
    "Zoey", "Aiden", "Layla", "Gabriel", "Nora", "Carter", "James", "Mary",
    "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David",
    "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
    "Thomas", "Sarah", "Christopher", "Karen", "Daniel", "Nancy", "Matthew",
    "Betty", "Anthony", "Sandra", "Jane",
})
LAST_NAMES = frozenset({
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson",
    "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
    "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez",
    "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright",
    "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams", "Nelson",
    "Baker", "Hall", "Rivera", "Campbell", "Mitchell", "Carter", "Roberts",
})
_NAME_RE = re.compile(r"\b([A-Z][a-zA-Z'-]+)\s+([A-Z][a-zA-Z'-]+)\b")


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _mask_cards(text: str) -> str:
    def replace(m: re.Match) -> str:
        digits = re.sub(r"[ -]", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            return "{{CARD}}"
        return m.group(0)

    return _CARD_CANDIDATE_RE.sub(replace, text)


def _mask_names(text: str) -> str:
    def replace(m: re.Match) -> str:
        if m.group(1) in FIRST_NAMES and m.group(2) in LAST_NAMES:
            return "{{NAME}}"
        return m.group(0)

    return _NAME_RE.sub(replace, text)


def mask_pii(text: str) -> str:
    """개인 식별정보를 토큰으로 치환한다.

    순서가 중요하다: CARD를 PHONE보다 먼저 처리해야 한다(구분자 없는
    13~19자리 카드번호 안에 우연히 10자리 전화번호 패턴이 들어있을 수
    있어, 먼저 통째로 카드로 소비해야 함). ADDRESS는 NAME보다 먼저 처리해
    "123 Main Street" 류가 이름으로 오탐되지 않게 한다.
    """
    text = _EMAIL_RE.sub("{{EMAIL}}", text)
    text = _mask_cards(text)
    text = _PHONE_RE.sub("{{PHONE}}", text)
    text = _ADDRESS_RE.sub("{{ADDRESS}}", text)
    text = _mask_names(text)
    return text
