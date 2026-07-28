import re
from pathlib import Path

# 정책 문서의 조항 헤더 형식: "## RET-02: Return Window by Tier"
_CLAUSE_RE = re.compile(r"^## ([A-Z]+-\d+): (.+)$", re.MULTILINE)


def parse_policy_doc(path: str) -> dict:
    """정책 마크다운 문서를 조항 단위로 파싱한다.

    각 조항(`## RET-02: ...` 형식의 헤더로 시작)을 하나의 단위로 추출한다.
    조항 번호가 인용 가능해야 하므로(save_draft 게이트 ④), 파싱 단계에서부터
    clause_id를 명시적으로 분리해 둔다.
    """
    p = Path(path)
    source = p.stem
    text = p.read_text(encoding="utf-8")

    matches = list(_CLAUSE_RE.finditer(text))
    clauses = []
    for i, m in enumerate(matches):
        clause_id = m.group(1)
        title = m.group(2)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        clauses.append({"clause_id": clause_id, "title": title, "body": body})

    return {"source": source, "clauses": clauses}
