# Claude Code 빌드 프롬프트 (Phase별)

> 전제: `CLAUDE.md`와 `DESIGN.md`가 컨텍스트에 로드돼 있음.
> 사용법: Phase 순서대로 아래 블록을 그대로 붙여넣기 → 완료 기준 확인 → `git commit` → 다음 Phase.
> 막히면 "이 단계를 설명하면서 같이 진행해줘"로 학습 모드 전환.

---

## Phase 0 — 개발 하네스 (★ 코드보다 먼저)

> 되돌릴 수 없는 사고를 먼저 막는다. 이 Phase를 건너뛰고 `app/`부터 만들지 말 것.

```
개발 하네스를 먼저 구축해줘. CS_CLAUDE_CODE_HARNESS_LOOP.md와 DESIGN.md 8절을 따른다.
훅 스크립트는 전부 Python(stdlib json만 사용, jq 의존성 없음)으로 작성한다.

1. .gitignore — .env, .claude/settings.local.json, evals/reports/, chroma_db/, data/synthetic/*.db
2. .claude/hooks/block_secrets.py      — PreToolUse(Write|Edit|Bash): API 키 패턴, .env 열람/전송 차단
3. .claude/hooks/block_protected_paths.py — PreToolUse(Write|Edit): evals/golden/, evals/runners/, data/raw/, .env 쓰기 차단
4. .claude/hooks/guard_eval_cost.py    — PreToolUse(Bash): evals/runners/ 의 --full/--all 실행 차단
5. .claude/hooks/log_tool_calls.py     — PostToolUse: .claude/agent-memory/dev/tool-log.jsonl 에 기록
6. .claude/settings.json — 위 훅 등록 + permissions.deny (Read(./.env), Edit(./evals/golden/**), Bash(rm -rf*), Bash(git push --force*))
7. .claude/rules/eval-integrity.md, .claude/rules/prompt-change-policy.md, .claude/rules/dev-loop.md
8. .claude/agents/eval-reviewer.md (Read/Grep만), .claude/agents/prompt-critic.md (Read + git diff만)
9. .env.example (실값 없는 플레이스홀더 템플릿)

차단 사유는 stderr로 출력하고 exit 2로 종료한다. 사유 문구에 "무엇을 대신 하면 되는지"를 포함할 것.

완료 기준(전부 실제로 시도해서 차단을 확인할 것 — 등록만 하고 검증 안 한 훅은 없는 것과 같다):
  a) evals/golden/ 아래 파일 수정 시도 → exit 2 확인
  b) 코드에 sk-로 시작하는 더미 키 쓰기 시도 → exit 2 확인
  c) cat .env 시도 → exit 2 확인
  d) evals/runners/run.py --full 실행 시도 → exit 2 확인
네 가지 차단 로그를 전부 보여주고 멈춰서 보고해줘.
```

---

## Phase 1 — 프로젝트 스캐폴딩

```
DESIGN.md 9절 구조에 따라 프로젝트 뼈대를 만들어줘.
- Python venv + requirements.txt
- FastAPI 비동기 서버, 모듈 분리 (common/rag, common/llm, modules/triage, modules/reply)
- prompts/ 디렉토리 (프롬프트는 코드에 인라인하지 않는다)
- Dockerfile + docker-compose (로컬 검증용)
- GET /health 엔드포인트
- tests/ 뼈대 + pytest 설정
이 Phase 범위만 작업하고 다음 단계는 건드리지 마.
완료 기준: 의존성 설치 + `docker compose up` 후 /health 200, `pytest -q` 통과. 확인되면 멈추고 보고해줘.
```

---

## Phase 2 — 데이터 준비

```
데이터를 준비해줘. 실제 고객 데이터는 절대 사용하지 않는다.

1. data/raw/ — Bitext Gen AI Chatbot Customer Support Dataset 다운로드 스크립트
   (scripts/download_bitext.py). 다운로드 후 data/raw/는 보호 경로이므로 이후 수정 금지.
2. data/synthetic/policies/ — 가상 이커머스사 정책 문서를 직접 합성.
   반품/교환/환불/배송/보증/멤버십 등급별 혜택 — 각 문서에 조항 번호를 달아 인용 가능하게 할 것.
3. data/synthetic/shop.db — 합성 주문/고객 SQLite.
   주문(주문번호, 상태, 배송사, 송장, 주문일, 금액), 고객(ID, 등급, 가입일).
   Bitext 티켓의 엔티티 슬롯과 매칭되도록 생성.
4. scripts/build_synthetic_data.py — 2~3을 재현 가능하게 생성 (시드 고정)

완료 기준: 정책 문서 N건 + 주문 M건 생성, 샘플 조회 동작 확인. 확인되면 멈추고 보고해줘.
```

---

## Phase 3 — RAG 인프라

```
공통 RAG 인프라를 구현해줘.
- 정책 문서 파싱 → 청킹(조항 단위 우선) → BGE-M3 임베딩 → ChromaDB 적재
- BGE-reranker로 재정렬
- 메타데이터 태깅: 문서명·조항번호 (인용 가능해야 함 — save_draft의 정책 인용 검사가 이걸 쓴다)
- 임베딩·리랭킹은 CPU
- 영구 컬렉션에는 합성 정책 문서만. 티켓 본문은 절대 적재하지 않는다
샘플 쿼리로 인덱싱 + 검색 동작 테스트 포함.
완료 기준: "반품 기한이 지났는데 환불 가능한가" 류 질의 → rerank된 관련 조항 반환. 확인되면 멈추고 보고해줘.
```

---

## Phase 4 — LLM 추상화 레이어 (정식 지원 경로)

```
LLM 호출 추상화 레이어를 만들어줘. DESIGN.md 7절을 따른다.
- app/common/llm/base.py: LLMBackend ABC (generate)
- app/common/llm/factory.py: get_llm_backend() / get_judge_backend() — 환경변수로 전환
- backends/anthropic.py: ChatAnthropic (기본 생성 백엔드, claude-opus-5)
- backends/openai.py: ChatOpenAI (기본 Judge 백엔드, gpt-5.6-luna)
- backends/ollama.py: 로컬 개발용
- LLM_BACKEND / JUDGE_BACKEND 는 서로 독립적으로 전환 가능해야 한다
- 키가 없거나 호출이 실패하면 조용히 폴백하지 말고 그대로 실패한다(fail-fast).
  신뢰도가 검증 안 된 백엔드로 조용히 갈아타면 게이트가 무의미해진다.
- 프롬프트 템플릿은 prompts/ 에서 로드 (코드 인라인 금지)

이번 Phase에서 커스텀 어댑터(RunPod)는 만들지 않는다 — Phase 8에서 별도로 한다.
완료 기준: 프롬프트 → 응답 수신, LLM_BACKEND/JUDGE_BACKEND 전환 동작, .env.example 갱신.
확인되면 멈추고 보고해줘.
```

---

## Phase 5 — Triage 모듈

```
티켓 분류 모듈을 구현해줘. 에이전트를 쓰지 않는다 — 단일 LLM 호출 + 구조화 출력이다.
- 입력: 티켓 본문 (PII 마스킹 후)
- 출력: {intent(27개 중 1개), category, confidence, requires_human, reason}
- app/common/privacy.py 의 mask_pii 를 모델 호출 "이전"에 적용
- confidence 는 LLM이 기록만 하고, requires_human 판정(임계값 비교)은 코드가 한다
- 인텐트 목록은 Bitext 라벨을 그대로 사용, prompts/triage_*.md 로 외부화

완료 기준: 샘플 티켓 20건 분류 → 인텐트/카테고리/confidence 출력.
마스킹이 모델 호출 전에 일어나는지 테스트로 확인. 확인되면 멈추고 보고해줘.
```

---

## Phase 6 — Reply Agent (ReAct + judge + validate)

```
답변 초안 생성 모듈을 LangGraph ReAct Agent로 구현해줘. DESIGN.md 2절 아키텍처를 따른다.

- State: ticket / triage / draft / judge_result / validation_passed / validation_feedback / budget / outcome
- 노드: plan → agent → judge → validate → (retry: agent | escalate | end)
- 도구 8개: search_policy / lookup_order / check_customer_tier / validate_draft_format /
            save_draft / discard_draft / escalate_to_human / submit_for_review
  [엄수] 모든 도구는 LLM 호출 없이 순수 계산·검색·저장만 수행한다. 추론과 문장 작성은 에이전트가 직접 한다.
- save_draft 결정론적 게이트 4종 (통과 시에만 저장, 거부 시 사유를 도구 응답으로 반환):
  ① PII 재유출 ② 근거 없는 확약(도구 결과에 없는 금액/날짜/환불 확약)
  ③ 금지 표현 블랙리스트 ④ 정책 인용 존재(정책 판단 인텐트인데 인용 0건이면 거부)
- judge 노드: get_judge_backend()로 정책 준수 + 톤 채점. app/modules/reply/judge.py 의 함수를 호출하며,
  이 함수는 나중에 오프라인 eval도 동일하게 호출한다(검증-배포 일치).
- validate 노드: 코드가 결정론적으로 판정 — judge threshold, 근거 인용 존재, PII 검사, 개수/형식
- 종료 상태 3종: auto_draft / escalated / failed.
  budget 소진 시 미달 초안을 내보내지 말고 escalated 로 종료한다.
- 재시도 시 validation_feedback 을 프롬프트에 주입해 같은 실수를 반복하지 않게 한다
- turn cap 과 budget 으로 무한루프 방지
- 출력에 "상담원 최종 책임(보조수단)" 고지 포함

완료 기준: 샘플 티켓 → 초안 생성 + 정책 인용 + judge 채점 + 코드 검증.
에스컬레이션 케이스(정책 밖 요구)도 실제로 escalated 로 끝나는지 통합 테스트로 확인.
확인되면 멈추고 보고해줘.
```

---

## Phase 7 — 평가 체계

> **Judge 신뢰도부터.** Judge가 못 믿을 상태에서 나머지 수치를 쌓으면 전부 다시 해야 한다.

```
평가 체계를 구축해줘. DESIGN.md 5절을 따른다. 합성/공개 데이터만 사용.

1. 골든셋 (evals/golden/*.jsonl — 만든 뒤에는 보호 경로):
   - triage_golden: Bitext 라벨 기반 200건
   - policy_violation_golden: 위반을 심은 답변 50건 (위반 유형 라벨 포함)
   - tone_golden: 생성 답변 30건에 사람이 5점 척도 라벨링
   - escalation_golden: 사람 개입이 필요한 케이스 30건 (라벨: should_escalate)
   - retrieval_golden: 질의 → 정답 조항 30건
2. evals/runners/ (보호 경로):
   - run_triage.py    — 인텐트 accuracy / macro-F1 / 카테고리 accuracy / confidence 캘리브레이션
   - run_reply.py     — PII FN율, 정책 위반 Recall/F1, 근거없는 확약률, 톤 Judge 평균,
                        에스컬레이션 Recall/FP율, 평균 반복수·도구호출수·latency
   - run_judge_reliability.py — 사람 라벨 대비 Cohen's κ, ±1 일치율
   - run_retrieval.py — Recall@5, MRR
   - check_thresholds.py — 임계 미달 시 exit 1
   - 모든 러너는 --sample N (기본 20) / --full 을 받는다
3. [엄수] eval은 고정 출력을 채점하지 말고 실제로 파이프라인을 돌려 새 초안을 생성한 뒤 채점한다.
4. 결과는 evals/reports/ 에 쓰고, 요약을 EVAL.md 에 이력으로 추가한다.

가장 먼저 run_judge_reliability.py 를 돌려 κ를 측정해줘. κ가 0.4 미만이면
다른 지표를 게이트로 쓰지 말고, 원인 가설과 함께 멈추고 보고해줘.
완료 기준: κ 측정 결과 + --sample 20 스모크 리포트 출력. 확인되면 멈추고 보고해줘.
```

---

## Phase 8 — 커스텀 어댑터 경로 + VENDOR_INTEGRATION.md

```
같은 LangGraph 파이프라인에 커스텀 벤더 어댑터를 붙여줘. DESIGN.md 7절을 따른다.

1. app/common/llm/backends/chat_runpod.py — BaseChatModel 직접 상속:
   - _generate / _agenerate
   - bind_tools (convert_to_openai_tool 로 LangChain 도구 → OpenAI 호환 tool schema)
   - 메시지 변환 양방향: LangChain BaseMessage ↔ 벤더 포맷
     (role 매핑, tool_calls 구조 변환, tool_call_id 처리)
   - 비동기 job queue 폴링: /run 제출 → job_id 확보 → 동일 job_id 폴링.
     [엄수] 제출 응답을 못 받아도 재제출하지 말고 명확한 예외로 상위에 알린다 (중복 실행 방지)
2. backends/runpod.py — 실제 HTTP 통신 레이어 (ChatRunPod은 이 위의 래퍼)
3. LLM_BACKEND=runpod 로 전환 시 reply agent가 그대로 동작해야 한다
4. VENDOR_INTEGRATION.md 작성:
   - 판단 기준표 (정식 지원으로 충분 vs 커스텀 어댑터 필요)
   - 왜 Anthropic/OpenAI/Bedrock은 공식 클래스로 충분하고 RunPod은 아닌가 (동기 vs job queue)
   - 커스텀 어댑터가 실제로 구현해야 했던 것들 — 코드 인용과 함께
   - 두 경로의 동작 비교 (같은 티켓, 같은 파이프라인, 백엔드만 전환한 결과)

먼저 로컬 Ollama 백엔드로 배선을 검증한 뒤 RunPod에 적용해줘.
완료 기준: 백엔드 전환만으로 동일 파이프라인 동작 + VENDOR_INTEGRATION.md 작성.
확인되면 멈추고 보고해줘.
```

---

## Phase 9 — 상담원 검토 UI (Next.js)

```
Next.js로 상담원 검토 UI를 만들어줘.
- 티켓 입력 → 분류 결과(인텐트/카테고리/confidence) 표시
- outcome 별 분기 렌더링:
  · auto_draft → 초안 + 인용된 정책 조항 + 사용한 도구 목록 + 편집/승인 버튼
  · escalated → 초안 없이 에스컬레이션 사유만 (초안을 흐릿하게라도 보여주지 말 것)
  · failed → 오류 표시 (내부 상세는 노출하지 않는다)
- 초안 하단에 "상담원 최종 책임(보조수단)" 고지 상시 표시
- SSE로 진행 상황 스트리밍 (도구 호출·채점·검증 단계)
- Next.js 서버가 FastAPI를 프록시하고, 서버 간 CS_API_KEY 인증

완료 기준: 브라우저에서 세 가지 outcome 이 전부 end-to-end 로 재현되는지 확인.
확인되면 멈추고 보고해줘.
```

---

## Phase 10 — 배포 · CI · 하네스 회고

```
배포와 CI를 마무리해줘. 학습 목적이니 인프라 단계는 무엇을 왜 하는지 설명하면서 같이 가자.

(a) GitHub Actions 경량 CI (.github/workflows/ci.yml) — 매 PR 블로킹:
    pytest 순수 로직 유닛테스트 + 백엔드 import 스모크 + 프론트 lint/build. 모델 호출 없음.
    [엄수] 전체 eval은 CI에서 자동 실행하지 않는다 (변동성 + 비용). 사람이 실행한다.
(b) Docker 이미지화 → docker-compose 로컬 검증 → 클라우드 VM 배포
(c) Caddy 리버스 프록시 + HTTPS
(d) billing alarm 설정 (LLM API 종량제)
(e) HARNESS_ENGINEERING.md 작성:
    - 훅으로 실제 차단된 사례 (tool-log.jsonl 기반)
    - MEMORY.md 에 쌓인 반복 실패 패턴과 훅 승격 사례
    - 루프 A/B/C 의 실제 종료·포기 동작 기록
    - 제품 가드레일과 개발 가드레일의 대칭성에 대한 회고
[엄수] 로그에 PII 금지 재확인, .env/시크릿 커밋 금지

완료 기준: 공개 HTTPS URL 접속 → 세 가지 outcome 동작 + CI 그린 + 문서 작성.
확인되면 멈추고 보고해줘.
```

---

## 진행 원칙

- **한 Phase = 한 커밋.** 완료 기준 통과 후에만 다음으로.
- **프롬프트(`prompts/`) 변경은 반드시 단독 커밋으로 분리한다.** 코드 변경과 섞이면 eval 점수 변화의 원인을 분리할 수 없다.
- Phase마다 새 환경변수가 생기면 `.env.example`에 항목 추가.
- 보안 하드룰(마스킹 순서·비저장·로그 PII 금지·근거 없는 확약 금지)은 매 Phase 자동 적용 — 어기면 즉시 교정.
- 개발 중 eval은 `--sample 20` 스모크셋. 전체 실행은 사람이 한다.
- **구현 루프는 3회 실패 시 멈춘다.** 4번째 시도 대신 세 번의 가정과 오류를 보고한다.
- 막히는 단계는 "설명하며 진행"으로 전환해 학습.
