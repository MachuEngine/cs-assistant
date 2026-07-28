#!/usr/bin/env python3
"""PreToolUse(Write|Edit): 보호 경로 쓰기 차단.

evals/golden/, evals/runners/, data/raw/, .env 는 사람 승인 없이
에이전트가 수정할 수 없다 (CLAUDE.md ★ 보호 경로). 쓰기만 막고
읽기는 허용한다 — 실패 원인 진단에는 읽기가 필요하다.
"""
import json
import os
import sys

PROTECTED_DIRS = ("evals/golden/", "evals/runners/", "data/raw/")


def is_protected_env_file(basename: str) -> bool:
    if basename == ".env.example":
        return False
    return basename == ".env" or basename.startswith(".env.")


def main() -> int:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path:
        return 0

    normalized = file_path.replace(os.sep, "/")
    basename = os.path.basename(file_path)

    for protected in PROTECTED_DIRS:
        if protected in normalized:
            print(
                f"BLOCKED: '{protected}' 는 보호 경로입니다. 사람 승인 없이 수정할 수 없습니다. "
                "eval이 실패하면 정답을 고치지 말고 app/ 의 로직을 고치세요. "
                "정답셋 자체가 틀렸다고 판단되면 수정 대신 근거와 함께 사람에게 보고하세요.",
                file=sys.stderr,
            )
            return 2

    if is_protected_env_file(basename):
        print(
            "BLOCKED: .env 는 보호 경로입니다. 시크릿은 .env 에만 두고 커밋하지 않습니다. "
            "새 환경변수가 필요하면 .env.example 에 플레이스홀더로 추가하세요.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
