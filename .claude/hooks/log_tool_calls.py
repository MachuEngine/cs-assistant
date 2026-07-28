#!/usr/bin/env python3
"""PostToolUse(Write|Edit|Bash): 도구 호출 관측 로그.

어떤 파일을 몇 번 반복해서 고쳤는지 보여주는 원본 데이터.
반복 실패 패턴 탐지 및 훅 승격 판단에 쓴다 (.claude/rules/dev-loop.md).
이 로그는 .gitignore 대상이며 커밋되지 않는다.
"""
import datetime
import json
import os
import sys

LOG_PATH = os.path.join(".claude", "agent-memory", "dev", "tool-log.jsonl")


def main() -> int:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input", {})

    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tool": payload.get("tool_name", ""),
        "path": tool_input.get("file_path", ""),
    }

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
