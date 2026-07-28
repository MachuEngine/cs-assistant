# prompts/

프롬프트·Judge 루브릭을 코드에 인라인하지 않고 이 디렉토리에서 로드한다(CLAUDE.md).

## 컨벤션

- 파일명: `{모듈}_{용도}.md` — 예: `triage_classify.md`, `judge_reply.md`
- 언어: 영어 (DESIGN.md 0절 — 파이프라인은 영어)
- **프롬프트 변경은 코드 변경과 분리된 단독 커밋**으로 만든다 (`.claude/rules/prompt-change-policy.md`)

실제 프롬프트 파일은 Phase 4(triage) · Phase 6(reply agent · judge)에서 추가된다.
