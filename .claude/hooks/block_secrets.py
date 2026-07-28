#!/usr/bin/env python3
"""PreToolUse(Write|Edit|Bash): 시크릿 유출 차단.

API 키로 보이는 문자열이 쓰이거나, .env를 직접 열람·전송하는 명령을
차단한다. permissions.deny(Read(./.env))가 Read 도구를 막아도
`cat .env`, `curl ... < .env` 같은 Bash 우회 경로는 별도로 잡아야 한다.
"""
import json
import re
import sys

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
]

# .env 를 직접 열람/전송하는 명령. 부정형 후방탐색으로 .env.example 은 통과시킨다.
ENV_ACCESS_RE = re.compile(
    r"\b(cat|less|head|tail|curl|scp|nc)\b[^|;&\n]*\.env(?!\.example)(?![.\w])"
)


def main() -> int:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input", {})
    content = (
        tool_input.get("content")
        or tool_input.get("new_string")
        or tool_input.get("command")
        or ""
    )

    for pattern in SECRET_PATTERNS:
        if pattern.search(content):
            print(
                "BLOCKED: API 키로 보이는 문자열이 포함되어 있습니다. "
                "실제 값을 코드에 쓰지 말고 os.environ[...] 로 .env 를 참조하도록 바꾸세요.",
                file=sys.stderr,
            )
            return 2

    if ENV_ACCESS_RE.search(content):
        print(
            "BLOCKED: .env 파일을 직접 열람·전송하는 명령입니다. "
            "필요한 값의 이름/형식만 확인하려면 .env.example 을 참조하고, "
            "코드에서는 os.environ 으로 읽으세요.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
