# CS 티켓 어시스턴트 — Claude Code 개발 하네스/루프 엔지니어링 셋업

> **적용 대상 명확화**
> 이 문서는 CS 에이전트 *제품*의 내부 구조에 관한 것이 아니다.
> **내가 Claude Code로 이 레포를 개발하는 과정** 자체를 하네스(무엇을 할 수 있는가)와
> 루프(어떻게 반복하는가)로 설계하는 문서다.
>
> - 제품 쪽 ReAct/가드레일 설계 → `CS_PROJECT_NOTES.md`
> - 개발 환경 쪽 훅/권한/재시도 → 이 문서

---

## 0. 왜 이걸 먼저 하는가

CS 프로젝트는 다음 특성 때문에 개발 하네스가 특히 필요하다.

| 특성 | 하네스가 없으면 생기는 문제 |
|---|---|
| 합성 정책 문서 + Bitext 원본 데이터가 레포에 있음 | Claude Code가 골든셋/테스트 픽스처를 "고쳐서 통과시키는" 사고 |
| API 키(OpenAI/Anthropic/RunPod) 사용 | 키가 커밋되거나 로그에 찍힘 |
| eval 비용이 실제 돈 | 무한 재시도 루프가 토큰을 태움 |
| 프롬프트가 소스코드 | 프롬프트를 조용히 바꿔서 eval 점수가 올라감 → 원인 추적 불가 |

핵심 원칙 하나: **eval의 정답(ground truth)과 eval 실행 코드는 에이전트가 수정할 수 없어야 한다.**
이게 이 문서 전체에서 가장 중요한 한 줄이다.

---

## 1. 초기 개발 세팅

### 1.1 레포 구조

```
cs-ticket-assistant/
├── CLAUDE.md                       # Layer 1: 프롬프트 레벨 규칙
├── .claude/
│   ├── settings.json               # 공유 훅/권한 (커밋함)
│   ├── settings.local.json         # 개인 오버라이드 (gitignore)
│   ├── hooks/
│   │   ├── block-protected-paths.sh   # PreToolUse: 보호 경로 차단
│   │   ├── block-secrets.sh           # PreToolUse: 키/시크릿 유출 차단
│   │   ├── guard-eval-cost.sh         # PreToolUse: 전체 eval 무단 실행 차단
│   │   └── log-tool-calls.sh          # PostToolUse: 관측성 로그
│   ├── rules/
│   │   ├── eval-integrity.md
│   │   └── prompt-change-policy.md
│   ├── agents/
│   │   ├── eval-reviewer.md        # eval 결과만 읽고 진단, 코드 수정 금지
│   │   └── prompt-critic.md        # 프롬프트 diff만 검토
│   └── agent-memory/
│       └── dev/MEMORY.md           # 반복 실패 패턴 누적
├── src/
│   ├── graph/                      # LangGraph 노드
│   ├── tools/                      # lookup_order_status 등
│   ├── prompts/                    # 프롬프트 = 버전 관리 대상
│   └── vendors/                    # ChatOpenAI / 커스텀 어댑터
├── evals/
│   ├── golden/                     # ★ 보호 경로: 정답셋
│   ├── runners/                    # ★ 보호 경로: eval 실행 스크립트
│   └── reports/                    # 실행 결과 (쓰기 허용)
├── data/
│   ├── raw/                        # ★ 보호 경로: Bitext 원본
│   └── synthetic/                  # 합성 정책 문서 (생성물, 쓰기 허용)
└── .env.example                    # 실제 .env는 gitignore
```

**보호 경로(★)의 정의**: 에이전트의 판단으로 바꿀 수 없고, 사람이 명시적으로 승인해야 바뀌는 경로.

### 1.2 세팅 순서 (이 순서대로)

1. `.gitignore` 먼저 (`.env`, `settings.local.json`, `evals/reports/`, `.claude/agent-memory/`)
2. `.claude/hooks/block-secrets.sh` — 가장 되돌릴 수 없는 사고를 먼저 막는다
3. `.claude/hooks/block-protected-paths.sh`
4. `.claude/settings.json`에 훅 등록
5. **차단 검증**: 일부러 `evals/golden/*.jsonl`을 고치게 시켜서 실제로 exit 2가 나는지 확인
6. `CLAUDE.md` 작성
7. 그다음에야 `src/` 첫 코드 작성

> 5번을 건너뛰지 말 것. 등록만 하고 검증 안 한 훅은 없는 것과 같다.

---

## 2. 하네스 레이어

### 2.1 보호 경로 차단 훅

`.claude/hooks/block-protected-paths.sh`:

```bash
#!/bin/bash
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty')
[ -z "$FILE_PATH" ] && exit 0

PROTECTED=(
  "evals/golden/"
  "evals/runners/"
  "data/raw/"
  ".env"
)

for p in "${PROTECTED[@]}"; do
  if [[ "$FILE_PATH" == *"$p"* ]]; then
    echo "BLOCKED: 보호 경로($p)는 사람 승인 없이 수정 불가. \
eval이 실패하면 정답을 고치지 말고 src/ 를 고칠 것." >&2
    exit 2
  fi
done
exit 0
```

`matcher`는 `Write|Edit|MultiEdit`에 건다. (Read는 허용 — 실패 원인 진단에 필요)

**왜 중요한가**: eval 점수가 안 오를 때 LLM이 가장 쉽게 찾는 해법은 "테스트를 완화하는 것"이다.
CLAUDE.md에 "골든셋 수정 금지"라고 적어도 컨텍스트가 길어지면 잊는다. 훅은 잊지 않는다.

### 2.2 시크릿 유출 차단 훅

`.claude/hooks/block-secrets.sh`:

```bash
#!/bin/bash
INPUT=$(cat)
CONTENT=$(echo "$INPUT" | jq -r '.tool_input.content // .tool_input.new_string // .tool_input.command // empty')

# API 키 형태 패턴
if echo "$CONTENT" | grep -qE '(sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})'; then
  echo "BLOCKED: API 키로 보이는 문자열. .env 참조 방식으로 바꿀 것." >&2
  exit 2
fi

# .env를 통째로 출력/전송하는 명령
if echo "$CONTENT" | grep -qE '(cat|less|head|tail|curl).*\.env([^.]|$)'; then
  echo "BLOCKED: .env 직접 열람/전송 금지. .env.example을 참조할 것." >&2
  exit 2
fi
exit 0
```

`matcher`: `Write|Edit|Bash`

### 2.3 eval 비용 가드

전체 eval(26,872건 기반 서브셋이라도)은 실제 API 비용이 나간다.
에이전트가 "확인 삼아" 전체 실행을 반복하지 않게 막는다.

`.claude/hooks/guard-eval-cost.sh`:

```bash
#!/bin/bash
INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# --full 또는 샘플 수 미지정 전체 실행 차단
if echo "$CMD" | grep -qE 'evals/runners/.*(--full|--all)'; then
  echo "BLOCKED: 전체 eval은 사람이 직접 실행. 개발 루프에서는 \
--sample 20 스모크셋을 사용할 것." >&2
  exit 2
fi
exit 0
```

원칙: **개발 루프 = 스모크셋(20~50건), 사람 승인 = 전체셋.**
루프의 반복 단가를 낮게 유지하는 게 루프 설계의 전제조건이다.

### 2.4 관측성: PostToolUse 로그

```bash
#!/bin/bash
INPUT=$(cat)
echo "$(date -Iseconds) $(echo "$INPUT" | jq -c '{tool: .tool_name, path: .tool_input.file_path}')" \
  >> .claude/agent-memory/dev/tool-log.jsonl
exit 0
```

이 로그는 나중에 "어떤 파일을 몇 번 반복해서 고쳤는가"를 보여준다 → 반복 실패 패턴 탐지의 원본 데이터.

### 2.5 settings.json

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [{ "type": "command", "command": ".claude/hooks/block-protected-paths.sh" }]
      },
      {
        "matcher": "Write|Edit|Bash",
        "hooks": [{ "type": "command", "command": ".claude/hooks/block-secrets.sh" }]
      },
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": ".claude/hooks/guard-eval-cost.sh" }]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit|Bash",
        "hooks": [{ "type": "command", "command": ".claude/hooks/log-tool-calls.sh" }]
      }
    ]
  },
  "permissions": {
    "deny": [
      "Read(./.env)",
      "Edit(./evals/golden/**)",
      "Edit(./data/raw/**)",
      "Bash(rm -rf*)",
      "Bash(git push --force*)"
    ]
  }
}
```

> `permissions.deny`와 훅은 중복이지만 의도적이다. permissions는 선언적 1차 방어,
> 훅은 우회 경로(예: `python -c` 로 파일 쓰기)까지 잡는 2차 방어.

### 2.6 CLAUDE.md에 들어갈 규칙 (Layer 1)

훅으로 강제하는 것과 **별개로**, 판단 기준은 문서로 준다.

```markdown
## 개발 원칙
- eval이 실패하면 `evals/golden/`이 아니라 `src/`를 고친다. 정답셋이 틀렸다고
  판단되면 수정하지 말고 근거와 함께 사람에게 보고한다.
- 프롬프트(`src/prompts/`) 수정은 반드시 단독 커밋으로 분리한다.
  코드 변경과 섞이면 eval 점수 변화의 원인을 분리할 수 없다.
- 벤더 어댑터는 `src/vendors/` 인터페이스를 통해서만 호출한다.
  파이프라인 코드에서 `ChatOpenAI`를 직접 import 하지 않는다.
- 개발 중 eval은 `--sample 20` 스모크셋. 전체 실행은 사람이 한다.
- 새 의존성 추가 전 반드시 물어본다.
```

### 2.7 서브에이전트 분리

| 에이전트 | 역할 | 도구 권한 |
|---|---|---|
| `eval-reviewer` | eval 리포트를 읽고 실패 원인 가설 제시 | Read, Grep만 |
| `prompt-critic` | 프롬프트 diff만 보고 리스크 지적 | Read, Bash(git diff)만 |

**핵심**: 진단하는 주체와 고치는 주체를 분리한다.
같은 컨텍스트가 진단과 수정을 다 하면, 자기가 세운 가설을 방어하는 방향으로 코드를 고친다.
CS 프로젝트의 톤 judge / 정책 위반 검출처럼 **주관적 판정이 섞인 eval**에서 특히 위험하다.

---

## 3. 루프 레이어

### 3.1 CS 프로젝트에서 돌릴 루프 3종

| 루프 | 종료 조건 | 관찰 신호 | max_attempts | 에스컬레이션 |
|---|---|---|---|---|
| **A. 기능 구현 루프** | 단위 테스트 통과 | pytest 결과 | 3 | 실패 요약 후 중단 |
| **B. RAG 품질 루프** | Recall@5 ≥ 목표 | Ragas 스코어 | 2 | 청킹 전략 선택지 제시 후 사람 결정 |
| **C. 가드레일 루프** | 위반 검출 F1 ≥ 목표 & 톤 judge ≥ 임계 | eval 리포트 | 2 | 반드시 사람 리뷰 (오검출 트레이드오프는 사람 판단 영역) |

루프 C는 **자동 종료를 만들지 않는다.** 정책 위반 검출은 FP/FN 균형이 제품 판단이지 기술 판단이 아니다.
이건 제품 쪽 HITL 설계와 같은 논리를 개발 워크플로우에도 그대로 적용하는 것이다.

### 3.2 루프 A 최소 구현

`scripts/dev_loop.py` — 로컬에서 Claude Code를 호출하는 형태가 아니라,
**Claude Code에게 이 루프의 규칙을 지키게 하는 형태**로 시작하는 게 현실적이다.

```markdown
<!-- .claude/rules/dev-loop.md -->
## 구현 루프 규칙
1. 테스트를 먼저 작성하고 실패를 확인한다.
2. 구현 → `pytest tests/ -x -q` 실행.
3. 실패 시: 실패 원인을 한 문장으로 `.claude/agent-memory/dev/MEMORY.md`에 append한 뒤 재시도.
4. **3회 실패하면 멈춘다.** 4번째 시도 금지. 다음을 보고한다:
   - 세 번의 시도에서 각각 무엇을 가정했고 무엇이 틀렸는지
   - 문제가 코드가 아니라 설계/스펙에 있을 가능성
5. 테스트를 완화하거나 skip 마킹해서 통과시키는 것은 실패로 간주한다.
```

4번이 루프 엔지니어링의 실질이다. 종료 조건보다 **포기 조건**이 설계하기 어렵고 더 중요하다.

### 3.3 last_failure_reason 주입

`MEMORY.md`에 단순 기록만 하면 다음 시도가 참조하지 않는다. 형식을 강제한다.

```markdown
<!-- .claude/agent-memory/dev/MEMORY.md -->
## 반복 실패 패턴
| 날짜 | 증상 | 잘못된 가정 | 실제 원인 | 훅 승격 여부 |
|---|---|---|---|---|
| 07-15 | tool_calls 파싱 실패 | 벤더별 응답 스키마가 같다고 가정 | RunPod은 job polling 후 payload 중첩 | X (1회) |
```

**훅 승격 규칙**: 같은 패턴이 3회 기록되면 `.claude/hooks/`의 차단 규칙으로 올린다.
"기억하기"에서 "실행 불가능하게 만들기"로 옮기는 것.

### 3.4 CI = 최종 게이트

로컬 루프는 스모크셋으로 빠르게 돌고, 전체 eval은 CI에서만 돈다.

```yaml
# .github/workflows/eval.yml
name: eval
on: [pull_request]
jobs:
  smoke:
    steps:
      - run: pytest tests/ -q
      - run: python evals/runners/run.py --sample 100
      - run: python evals/runners/check_thresholds.py   # 임계 미달 시 exit 1
```

`check_thresholds.py`도 보호 경로에 둔다. 임계값을 낮춰서 CI를 통과시키는 경로를 막는다.

---

## 4. 통합 로드맵

| 순서 | 층 | 항목 | 완료 기준 |
|---|---|---|---|
| 1 | 하네스 | `.gitignore` + 시크릿 차단 훅 | 일부러 키 쓰기 시도 → 차단 확인 |
| 2 | 하네스 | 보호 경로 차단 훅 | 골든셋 수정 시도 → exit 2 확인 |
| 3 | 하네스 | `CLAUDE.md` 개발 원칙 | — |
| 4 | 루프 | 구현 루프 규칙(`max_attempts=3`) | 일부러 못 푸는 태스크 → 3회 후 멈추는지 확인 |
| 5 | 하네스 | eval 비용 가드 | `--full` 실행 시도 → 차단 확인 |
| 6 | 하네스+루프 | `MEMORY.md` 형식 + 훅 승격 규칙 | 실패 1건이 실제로 기록되는지 |
| 7 | 하네스 | `eval-reviewer` 서브에이전트 | 코드 수정 시도 없이 진단만 나오는지 |
| 8 | 하네스 | CI 임계값 게이트 | 임계 미달 PR이 실제로 red 되는지 |
| 9 | 루프 | 에스컬레이션 경로(PR 코멘트 알림) | — |
| 10 | 하네스 | 권한 축소(`permissions.allow` 화이트리스트) | 마지막. 너무 일찍 하면 개발이 막힘 |

각 단계의 완료 기준이 전부 "차단되는지 확인"인 게 의도적이다. 설정 파일 작성은 완료가 아니다.

---

## 5. 포트폴리오 관점

이 셋업 자체가 면접 소재가 된다. 정리해둘 문장:

- "에이전트 제품에 가드레일을 넣는 것과, 에이전트로 개발하는 과정에 가드레일을 넣는 건
  같은 문제다. 둘 다 모델의 협조에 의존하지 않는 시스템 레벨 강제가 필요하다."
- "eval 골든셋을 에이전트가 수정 못 하게 훅으로 막았다. 프롬프트로 '고치지 마'라고 하는 것과
  도구 호출 자체를 차단하는 건 신뢰도가 다르다."
- "루프의 종료 조건보다 포기 조건 설계가 어려웠다. 3회 실패 시 멈추고 가정을 보고하게 만든 게
  실제로 설계 결함을 조기에 드러냈다."

`HARNESS_ENGINEERING.md`로 레포에 남기면 `VENDOR_INTEGRATION.md`와 함께
"툴을 쓸 줄 안다"를 넘는 두 번째 문서가 된다.

---

## 6. 미결정 사항

- 훅 스크립트를 bash로 갈지 python으로 갈지 (jq 의존성 vs 이식성)
- `agent-memory/`를 커밋할지 (실패 패턴이 포트폴리오 자산이 될 수도 있음)
- 루프 B/C의 구체적 임계값 — Bitext 서브셋으로 베이스라인 측정 후 결정
