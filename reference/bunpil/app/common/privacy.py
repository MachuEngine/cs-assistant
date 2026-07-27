"""사용자 입력 개인정보 마스킹.

모델·외부 서비스 호출 전에 적용하는 공통 보안 경계다. 정규식 마스킹은
실수로 포함된 명시적 식별자를 줄이는 2차 방어선이며, 실제 학생 데이터 입력
금지 원칙을 대체하지 않는다.
"""
import re
from typing import List, Tuple


_PHONE = re.compile(
    r"(?<!\d)(?:01[016789]|02|0[3-6][1-5]?)[ .-]?\d{3,4}[ .-]?\d{4}(?!\d)"
)
_JUMIN = re.compile(r"(?<!\d)\d{6}\s*-?\s*[1-4]\d{6}(?!\d)")
_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_SCHOOL = re.compile(r"[가-힣]{1,8}(고등학교|중학교|초등학교)")
_STUDENT_ID = re.compile(r"(?<!\d)\d{5,10}(?!\d)")

# 이름은 일반 명사와 구분하기 어려우므로 명시적 레이블·학생 호칭·연락처 문맥만 처리한다.
_LABELED_NAME = re.compile(r"(?:학생\s*이름|이름|성명)\s*[:：]?\s*[가-힣]{2,4}")
_STUDENT_NAME = re.compile(
    r"(?<![가-힣])(?!(?:해당|다른|모든|여러|개별|우리|학급|학년)\s)"
    r"([가-힣]{2,4})(?=\s*(?:학생|군|양)(?:은|는|이|가|의|을|를|에게|과|와)?(?:\s|[,.()]))"
)
_NAME_WITH_CONTACT = re.compile(
    r"(?<![가-힣])([가-힣]{2,4})(?=\s*\(\s*(?:\[연락처\]|\[이메일\]))"
)

# 주소 전체를 무리하게 추정하지 않고 레이블 또는 '거주' 문맥이 명확할 때만 처리한다.
_LABELED_ADDRESS = re.compile(r"(?:주소|거주지)\s*[:：]?\s*[^\n,;]{4,60}")
_RESIDENCE = re.compile(
    r"(?:서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|"
    r"울산광역시|세종특별자치시|제주특별자치도|[가-힣]+도)"
    r"(?:\s+[가-힣0-9]+(?:시|군|구|동|읍|면|로|길)){1,4}"
    r"(?:\s+\d+(?:-\d+)?)?(?=\s*(?:에\s*)?거주)"
)

_RULES: List[Tuple[re.Pattern, str, str]] = [
    (_JUMIN, "주민번호", "[주민번호]"),
    (_PHONE, "전화번호", "[연락처]"),
    (_EMAIL, "이메일", "[이메일]"),
    (_SCHOOL, "학교명", "[학교]"),
    (_STUDENT_ID, "학번", "[학번]"),
    (_LABELED_ADDRESS, "주소", "[주소]"),
    (_RESIDENCE, "주소", "[주소]"),
]


def _add_found(found: List[str], label: str) -> None:
    if label not in found:
        found.append(label)


def mask_pii(text: str) -> Tuple[str, List[str]]:
    """PII를 마스킹한 텍스트와 발견된 PII 유형 목록을 반환한다."""
    found: List[str] = []
    masked = text

    for pattern, label, placeholder in _RULES:
        if pattern.search(masked):
            _add_found(found, label)
            masked = pattern.sub(placeholder, masked)

    for pattern in (_LABELED_NAME, _STUDENT_NAME, _NAME_WITH_CONTACT):
        if pattern.search(masked):
            _add_found(found, "이름")
            masked = pattern.sub("[이름]", masked)

    return masked, found
