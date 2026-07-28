#!/usr/bin/env python3
"""PreToolUse(Bash): evals/runners/ 전체 실행(--full/--all) 차단.

개발 루프 = 스모크셋(--sample N). 전체 eval은 실제 API 비용이 나가고
무한 재시도 루프가 토큰을 태울 수 있어 사람이 직접 실행한다.
"""
import json
import re
import sys

FULL_RUN_RE = re.compile(r"evals/runners/\S+.*--(full|all)\b")


def main() -> int:
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")

    if FULL_RUN_RE.search(command):
        print(
            "BLOCKED: evals/runners/ 의 전체 실행(--full/--all)은 사람이 직접 실행합니다. "
            "개발 루프에서는 --sample 20 스모크셋을 사용하세요.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
