# 루프 엔지니어링 × 하네스 엔지니어링 적용 가이드

> Agent = Model + Harness + Loop
> 모델이 무엇을 "생각"하는지는 통제할 수 없지만, 무엇을 "할 수 있는지"(하네스)와 "언제까지, 어떻게 반복하는지"(루프)는 시스템 레벨에서 설계할 수 있다.

---

## 0. 두 개념의 경계선

| 구분 | 하네스 (Harness) | 루프 (Loop) |
|---|---|---|
| 질문 | 에이전트가 무엇을 "할 수 있는가" | 에이전트가 "어떻게 반복하는가" |
| 다루는 것 | 권한, 파일 접근, 훅, 관측성, 메모리 저장소 | 종료 조건, 재시도 전략, 반영(reflection), 에스컬레이션 |
| 실패 시 결과 | 행동 자체가 차단됨 (exit 2) | 같은 행동을 다른 전략으로 다시 시도하거나 사람에게 넘김 |
| 예시 | PreToolUse 훅으로 민감 파일 접근 차단 | 테스트 통과할 때까지 코드 수정 반복 |

두 층은 독립적으로 작동해야 한다. 하네스가 "이건 절대 못 건드림"을 강제하는 동안, 루프는 "허용된 범위 안에서 몇 번, 어떤 조건으로 시도할지"를 결정한다.

---

## 1. 하네스 엔지니어링 — Layer 2~5 구현

### 1.1 폴더 구조 (Claude Code 기준)

```
project/
├── CLAUDE.md                    # Layer 1: 프롬프트 레벨 규칙 문서
├── .claude/
│   ├── settings.json            # 팀 공유 훅/권한 정책
│   ├── settings.local.json      # 개인 오버라이드 (gitignore)
│   ├── hooks/
│   │   ├── block-sensitive-data.sh   # PreToolUse: 민감 데이터 파일 차단
│   │   └── enforce-constraint.sh     # PreToolUse: 특정 실행 제약 강제
│   ├── rules/
│   │   └── sensitive-data.md    # 민감 데이터 관련 파일 건드릴 때만 적용
│   ├── agents/
│   │   └── eval-reviewer.md     # eval 결과만 검토, 코드는 수정 안 함
│   └── agent-memory/
│       └── <agent>/MEMORY.md    # 자동 생성/자동 갱신되는 실패 패턴 기록
```

### 1.2 PreToolUse 훅: 민감 데이터 차단 (Layer 2, 최우선 구현)

`.claude/hooks/block-sensitive-data.sh`:

```bash
#!/bin/bash
# stdin으로 JSON 형태의 tool call 정보를 받는다
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# 민감 데이터가 있는 경로 패턴
BLOCKED_PATTERNS=(
  "data/private/"
  "*.pii.csv"
  "records/personal/"
)

for pattern in "${BLOCKED_PATTERNS[@]}"; do
  if [[ "$FILE_PATH" == $pattern* ]] || [[ "$FILE_PATH" == *"$pattern"* ]]; then
    echo "BLOCKED: 민감 데이터 경로는 승인 없이 접근 불가 ($FILE_PATH)" >&2
    exit 2   # 2 = 도구 호출 차단, 모델에게 사유 전달
  fi
done

exit 0  # 허용
```

`.claude/settings.json`에 등록:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|Read",
        "hooks": [
          { "type": "command", "command": ".claude/hooks/block-sensitive-data.sh" }
        ]
      }
    ]
  }
}
```

핵심: 이 스크립트는 모델이 "민감 데이터는 안 건드릴게요"라고 다짐하는 것과 무관하게, 실제로 그 도구 호출 자체를 OS 레벨에서 막는다. 프롬프트 규칙(CLAUDE.md)이 이미 이 내용을 담고 있어도, 훅이 있어야 모델의 협조 여부와 무관하게 강제된다.

### 1.3 반복되는 실패 패턴을 하네스로 승격

`MEMORY.md`에 텍스트로만 적어두면 다음 세션에서 또 잊을 수 있다. 대신 도구 호출 자체를 검사하는 훅으로 승격시킨다. 예를 들어 특정 실행 환경 제약(예: 특정 컴포넌트는 반드시 어떤 조건에서만 실행되어야 함)이 있다면:

```bash
#!/bin/bash
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

if echo "$COMMAND" | grep -qE "<금지된 패턴>"; then
  echo "BLOCKED: 과거 실패 패턴과 일치하는 명령" >&2
  exit 2
fi
exit 0
```

이렇게 하면 "잊지 않도록 기억하기"가 아니라 "애초에 실행이 안 되게" 만든다 — 이게 하네스 엔지니어링의 핵심 차이. `MEMORY.md`에 실패 패턴이 3회 이상 반복 기록되면, 그 패턴을 훅으로 승격시키는 걸 정기 점검 루틴으로 삼는 것도 좋은 방법이다.

### 1.4 서브에이전트 분리: 검토 전용 에이전트

`.claude/agents/eval-reviewer.md`로 테스트/eval 결과만 읽고 판단하되 코드를 직접 수정하지 않는 에이전트를 분리한다. 이는 "eval-to-CI 연결" 단계의 중간 계층 역할을 한다 — CI가 최종 게이트라면, 이 서브에이전트는 CI 이전에 빠르게 피드백을 주는 로컬 게이트다.

### 1.5 권한 축소 (마지막 단계, 미리 설계만)

`settings.json`의 `permissions` 필드로 Bash 도구의 기본 허용 범위를 좁힌다:

```json
{
  "permissions": {
    "allow": ["Read(./src/**)", "Edit(./src/**)"],
    "deny": ["Read(./data/private/**)", "Bash(rm -rf*)"]
  }
}
```

---

## 2. 루프 엔지니어링 — 에이전트 실행 루프 설계

### 2.1 루프 설계의 3요소

1. **종료 조건 (Termination)**: 무엇이 성공이고, 무엇이 포기 신호인가
2. **관찰 신호 (Observation)**: 각 반복에서 다음 행동을 결정할 근거는 무엇인가
3. **에스컬레이션 (Escalation)**: 루프가 스스로 못 풀 때 누구에게, 어떻게 넘기는가

### 2.2 예시: 테스트 통과를 종료 조건으로 하는 자동 수정 루프 (LangGraph)

```python
from langgraph.graph import StateGraph, END

class LoopState(TypedDict):
    attempt: int
    max_attempts: int
    eval_result: dict | None
    last_failure_reason: str | None
    code_diff: str | None

def run_eval(state: LoopState) -> LoopState:
    # 테스트/eval 실행, 결과를 관찰 신호로 저장
    result = subprocess.run(["python", "eval_script.py"], capture_output=True)
    state["eval_result"] = parse_eval_output(result.stdout)
    return state

def should_continue(state: LoopState) -> str:
    if state["eval_result"]["passed"]:
        return "success"
    if state["attempt"] >= state["max_attempts"]:
        return "escalate"   # 사람에게 넘김
    return "retry"

def apply_fix(state: LoopState) -> LoopState:
    # 이전 실패 원인(last_failure_reason)을 MEMORY.md에서 참고해
    # 같은 실수를 반복하지 않도록 프롬프트에 포함
    state["attempt"] += 1
    return state

graph = StateGraph(LoopState)
graph.add_node("run_eval", run_eval)
graph.add_node("apply_fix", apply_fix)
graph.add_conditional_edges("run_eval", should_continue, {
    "success": END,
    "retry": "apply_fix",
    "escalate": END,  # 별도 알림 노드로 연결 가능
})
graph.add_edge("apply_fix", "run_eval")
```

핵심 설계 포인트:
- **`max_attempts`가 하드 리밋**: 루프가 무한히 도는 것 자체가 하네스가 막아야 할 위험이므로, 이 값도 `settings.json`이나 별도 config에서 관리해 모델이 스스로 늘릴 수 없게 한다.
- **`last_failure_reason`을 다음 시도에 명시적으로 전달**: 단순 재시도가 아니라 "왜 실패했는지"를 반영하는 게 reflection 루프의 핵심. 이 정보는 `agent-memory/<agent>/MEMORY.md`에도 누적해 다음 세션에서도 참고 가능하게 한다.
- **`escalate` 경로는 반드시 존재**: 루프가 스스로 못 풀면 자동으로 계속 시도하는 게 아니라, 사람의 승인/개입 지점으로 넘어가야 한다 (human-in-the-loop).

### 2.3 관찰 신호 설계 체크리스트 (일반화)

각 반복(iteration)에서 무엇을 관찰할지 명시적으로 정의:

| 신호 | 소스 | 루프가 하는 판단 |
|---|---|---|
| 테스트 통과 여부 | eval/test 스크립트 | 종료 vs 재시도 |
| 훅 차단 로그 | PreToolUse/PostToolUse 훅 stderr | "허용 범위 밖 시도"로 판단, 전략 전환 |
| 외부 API 응답 메타데이터 | 성능/응답시간 로그 | 제약 조건 위반 여부 판단 |
| 검색/리트리벌 품질 스코어 | 벡터스토어 eval 스코어 | 재인덱싱 필요 여부 |

---

## 3. 통합 로드맵 (일반화 순서)

1. **[하네스]** PreToolUse 훅으로 민감 데이터 파일 차단
2. **[하네스]** 기존 보안 하드룰을 훅 레벨로 승격 (반복된 실패 패턴 포함)
3. **[루프]** 테스트/eval을 종료 조건으로 하는 자동 재시도 루프 프로토타입 (`max_attempts`는 작게 시작, 예: 2~3)
4. **[하네스+루프]** `agent-memory/MEMORY.md`를 단순 기록이 아니라 루프의 관찰 신호로 연결 (이전 실패 원인을 다음 반복 프롬프트에 주입)
5. **[하네스]** eval 스크립트를 CI에 연결해 로컬 루프의 최종 게이트로 사용
6. **[루프]** 에스컬레이션 경로 구현 — N번 실패 시 알림 또는 PR 코멘트로 사람 개입 요청
7. **[하네스]** 권한 축소 → 네트워크 격리

---

## 4. 다음 실습 제안

가장 작고 검증 가능한 단위부터: 민감 데이터 차단 훅을 실제로 만들어서 `.claude/settings.json`에 등록하고, 의도적으로 그 파일을 Claude Code가 건드리게 시켜서 정말 차단되는지 확인하는 것부터 시작하는 걸 추천한다. 그다음 테스트 재시도 루프의 최소 버전(하드코딩된 `max_attempts=2`)을 붙여보면 두 층이 실제로 상호작용하는 걸 직접 볼 수 있다.
