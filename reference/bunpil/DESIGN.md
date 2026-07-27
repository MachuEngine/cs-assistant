# 사회 교사용 AI 어시스턴트 — 설계 스펙

> 포트폴리오 + 지인(고등학교 사회 교사) 실사용을 위한 LLM 서비스.
> 본 문서는 Claude Code 빌드 브리프(`CLAUDE.md`)이자 설계 요약본으로 사용한다.

---

## 1. 개요

- **목적**: 고등학교 사회 교사의 (1) 문제 출제, (2) 생기부 작성을 돕는 대화형 웹 서비스
- **사용 맥락**: 포트폴리오(Agent·RAG·평가·배포 실습) + 지인 교사 1인 실사용
- **모듈**: ② 출제 도우미(Agent), ③ 생기부 윤문 도우미(Chain)
- **보류**: ① 입시 상담 모듈 (최신성·데이터 부담 커서 1차 범위 제외)
- **데이터 원칙**: 실제 학생 데이터 미사용(전부 합성/익명), 사용자 입력 비저장(stateless), 공개 자료 + 소량 큐레이션

---

## 2. 아키텍처

### 모듈 ② 출제 도우미 — ReAct Agent (LangGraph)

과제 정의: *"교사가 붙여넣은 예시 문제의 구성(개수·유형·난이도)을 그대로 반영한 새 문항 세트 작성"*.
(2026.07 리디자인, `FEEDBACK_DRIVEN_REDESIGN_v2.md` — 실사용 교사 피드백: PDF 업로드+유형/난이도/개수
드롭다운 대신 ChatGPT처럼 예시 문제를 붙여넣는 사용 패턴이 실제와 더 맞았음. 2028 수능 개편으로 과목별
구조가 곧 무의미해질 `past_exams`/`check_duplicate`도 이때 완전히 제거)

```
예시 문제 붙여넣기(passage_text) → Agent(ReAct) ↔ 도구 → submit_for_review로 제출
  → judge_node(외부 Judge 백엔드)가 구조 유사도 채점 → 코드가 threshold 판정
  → 미달 시 세트 전체 재시도(budget) → 교사 검토 세트
```

> **2026-07-23 변경(옵션 B)**: 구조 유사도 채점을 생성 에이전트의 자기채점(self-judge)에서
> 별도 Judge 노드로 분리했다. 계기: 런타임 self-judge 신뢰도가 사람 라벨과 한 번도
> 대조된 적이 없었고, 오프라인 eval이 검증하는 Judge(`get_judge_backend()`)와 실제
> 배포된 judge(생성 모델 자신)가 서로 다른 코드 경로였다(검증-배포 불일치). 이제
> 런타임 `judge_node`도 오프라인 eval과 동일한 `app/modules/exam/judge.py`의
> `judge_structure()`를 호출해 두 경로가 항상 같은 judge를 측정한다. 트레이드오프:
> `JUDGE_BACKEND=openai`(기본값)에서는 매 문항 세트 생성마다 `passage_text`(PII
> 마스킹은 되지만 저작권 있는 교사 지문일 수 있음)가 OpenAI로 전송된다(6절 참고).

**도구(Tools)**

| 도구 | 역할 | 구현 |
|---|---|---|
| 성취기준 검색 | `search_standards` — 성취기준 원문 검색 | ChromaDB + Rerank |
| 법령 검색 | `search_regulations` — 교육과정 준수 사항 검색 | ChromaDB + Rerank |
| 형식 검증 | `validate_item_format` — 문항 형식 자기교정 | 함수 |
| 저장 | `save_item` — 검증 통과 문항 저장 | 함수 |
| 폐기 | `discard_item` — 승인 불가 문항을 ID로 제거 | 함수 |
| 자체 채점 | `record_score` — 품질 자체 평가 기록 | 함수 |
| 제출 신호 | `submit_for_review` — 작성 완료 신호(인자 없음) | 함수 |

**노드**: `plan → agent → judge → validate → (재시도: agent | 종료)`. `judge` 노드는 도구가
아니라 그래프 노드 — `agent`가 `submit_for_review`를 호출해 작성을 끝내면, `judge`가
`get_judge_backend()`(생성 모델과 별개 백엔드)로 구조 유사도를 채점한다.

**State**

```
spec:                    { passage_text(예시 문제 원문), num_items(생성 개수, 기본 2) }
draft_items:             [ { 문항, 유형, 난이도, judge_score, 상태 } ]
similarity_judge_result: { type_ratio_score, difficulty_match, overall_score } — judge_node가 기록
budget:                  남은 재시도 횟수 (세트 전체 단위, 무한루프 방지)
```

**Agent(LLM, 생성 모델)가 판단하는 것**: 문항 세트 작성, 형식 자기수정, 제출 시점 판단(`submit_for_review`).
**Judge(LLM, 별도 백엔드)가 판단하는 것**: 구조 유사도(유형 비율·난이도·종합 유사도) — `judge_node`.
**코드가 판단하는 것**: 문항 개수 일치 여부(`len(draft_items) == spec["num_items"]`), Judge 결과의 threshold 통과 여부, 재시도 여부.
→ "판단은 LLM, 통과/재시도 결정은 코드"라는 원칙은 유지하되, 2026-07-23부터 "판단하는 LLM"이
생성 모델(Agent)과 Judge로 분리됨(과거엔 생성 모델이 자기 출력을 자기가 판단했음).

> **2026-07-09 정정**: 문항 개수는 예시 문제(`passage_text`)의 문항 수와 무관하게 `num_items`로 별도 지정된다(사용자가 자연어로 명시하지 않으면 기본값 2 — 2026-07-21 5에서 축소, `main.py`가 LLM 판단으로 추출). 초기엔 "생성 개수가 예시 문제 개수와 일치해야 한다"는 전제로 `count_match`를 LLM Judge가 판단했으나, 이 전제 자체가 실제 설계와 맞지 않아 폐기 — 개수 일치는 이제 LLM Judge가 아니라 코드가 직접 검증한다.

> **2026-07-21 변경**: `standards`를 교사 입력으로 받던 것을 폐지 — UI에서 성취기준 입력창을 제거하고 `spec`에서도 해당 필드를 삭제했다. 대신 에이전트가 `search_standards` 도구로 문항 주제에 맞는 성취기준을 스스로 검색해 `save_item`의 `standard` 인자를 채운다(가능하면 검색, 관련 자료가 없으면 빈 값으로 진행 — 저장을 막지 않음).

### 모듈 ③ 생기부 윤문 도우미 — 검증 Chain (수동 루프)

```
관찰 메모 입력 → 개인정보 마스킹 → 윤문 생성 → 규정·사실보존 검증 → 안전 출력 + 책임 고지
                                                       └ 위반 시 재시도, 해소 실패 시 출력 보류
```

- **입력**: 교사가 직접 작성한 관찰 메모만 (학생 작성·제출분 금지)
- **윤문**: 생성이 아닌 "다듬기" — 메모에 없는 사실 추가 금지
- **규정·사실 검증**: 규정 RAG로 학교명 노출·과장·금지표현을 대조하고 NLI식 검증으로 메모에 없는 사실 추가를 차단
- **출력**: 모든 검증을 통과한 문장만 제공. 실패 시 윤문을 숨기고 사유를 표시하며, 교사 최종 책임 고지(보조수단)를 명시

---

## 3. 기술 스택

| 구분 | 선택 |
|---|---|
| 백엔드 | FastAPI (비동기) |
| 오케스트레이션 | LangGraph(출제 agent) / 수동 루프(생기부 chain, LangChain 컴포넌트를 직접 호출) |
| 벡터스토어 | ChromaDB + Rerank (BGE-reranker) |
| 임베딩 | BGE-M3 |
| LLM 서빙 | vLLM + Qwen2.5 (프로덕션) |
| Judge(평가) 모델 | OpenAI gpt-5.6-luna(기본, `JUDGE_BACKEND=openai`) / Ollama 로컬(대안) — 2026-07-23부터 오프라인 eval뿐 아니라 런타임(`judge_node`)에도 적용, 생성 백엔드와 완전히 독립. 채택 근거는 MODEL_SELECTION.md |
| 검증 | LLM as a Judge |
| 프롬프트 | Few-shot / CoT |
| 프론트엔드 | Next.js (frontend/) |

※ 임베딩·리랭킹은 앱 팟(CPU)에서 수행(소규모 코퍼스라 충분), **생성·추론만 서버리스 GPU 호출** → GPU 호출 최소화.

---

## 4. 데이터

| 항목 | 출처 | 방법 | 비고 |
|---|---|---|---|
| 생기부 기재요령 | 학교생활기록부 종합지원포털(star.moe.go.kr) 자료실 | PDF 다운로드 | 규정 RAG 핵심 |
| 학생부 작성·관리 지침(훈령) | 동 포털 | PDF | 규정 RAG |
| 사회과 성취기준 | 국가교육과정정보센터(NCIC) | 문서 조회 | `search_standards` RAG |
| 교사가 붙여넣은 예시 문제 | 교사 런타임 입력(`passage_text`) | 0 | ChromaDB 미적재, 프롬프트에만 사용 후 폐기 |
| 윤문 Few-shot 예시 | 직접 합성 | 가상 시나리오 | 실데이터 금지 |
| 규정 위반 테스트 문장 | 직접 합성 | 위반 일부 심기 | 평가용 |

**공통 파이프라인**: PDF 수집 → PyMuPDF 파싱 → 청킹 → BGE-M3 임베딩 → ChromaDB (메타데이터: 출처·연도 태깅)

**⛔ 절대 수집 금지**: 실제 학생 생기부, 실제 내신 답안, 식별 가능한 학생 정보.

---

## 5. 평가 설계

**원칙**: ① LLM Judge는 사람 라벨과 먼저 일치율 검증 ② 정량(함수)/정성(Judge·사람) 분리 ③ 실제 컬렉션 기반 골든셋.

### 출제 Agent

| 계층 | 지표 | 판정 | 통과 기준(시작값) |
|---|---|---|---|
| 검색 | Recall@5, MRR | 함수 | R@5 ≥ 0.8 |
| 문항 | 정답 유일성·오답 매력도·근거성 | LLM Judge | 5점 척도 평균 ≥ 4.0 (보정 후 확정) |
| 구조 유사도 | type_ratio_score·difficulty_match·overall_score (LLM Judge) + 문항 개수 일치(코드) | LLM Judge(개수 제외) + 코드(개수) | diff κ ≥0.4 달성(0.424) / overall 이진 κ 0.4 미달(0.167) — 열린 이슈, EVAL.md 5·6절. 2026-07-23부터 이 Judge가 런타임 judge_node와 동일 코드라 이 수치가 곧 배포된 judge의 신뢰도임 |
| 과정 | 평균 반복수·미충족 실패율·latency | 함수 | 예산 내 수렴 |
| 종단 | 수정 없는 교사 채택률 | 사람 | 북극성 |

검색 골든셋(`data/golden/retrieval_golden_final.json`): `standards` / `regulations` 실제 컬렉션에서 샘플링한 22개 청크(reviewed 21개). `golden_gen/gen_golden_retrieval.py`로 초안 생성 후 검수. (2026.07 리디자인으로 `past_exams` 참조 8개 제거, 30→22)

### 생기부 Chain (안전 지표 우선)

| 우선 | 지표 | 판정 | 통과 기준 |
|---|---|---|---|
| 🔴 | 마스킹 누락률(FN) | 함수 | 0 |
| 🔴 | 사실 추가율(메모에 없는 내용) | LLM Judge(NLI식) | 0 |
| 🔴 | 규정 위반 검출 Recall/F1 | 함수 | Recall ≥ 0.95 |
| 🟡 | 문체 적합성 | LLM Judge | 5점 척도 평균 ≥ 4.0 |
| 🟢 | 교사 채택률·수정량 | 사람 | 북극성 |

**모델 채택 근거**: 생성 모델(Qwen2.5-7B→14B)·Judge 모델(qwen2.5 계열→gpt-5.6-luna) 모두
위 평가셋으로 정량 비교 후 채택 — 상세 비교 데이터·판단 근거는 [MODEL_SELECTION.md](./MODEL_SELECTION.md) 참고.

**골든셋 현황**: 출제 검색 22개(21개 검수 완료) + STRUCTURE_GOLDEN 45개(사람 라벨링 전량 완료) / 생기부(위반문장 50 + 마스킹 20 + 메모→윤문 20). 모든 골든셋은 `data/golden/*.json`으로 외부화(하드코딩 금지).

> **2026-07-09 num_ctx 발견**: STRUCTURE_GOLDEN 재생성 중 로컬 Ollama가 기본 `num_ctx=4096`으로 돌고 있어(모델은 32K 네이티브 지원) 멀티턴 ReAct 루프의 검색 결과 누적이 몇 턴 만에 컨텍스트를 초과시키고, 컨텍스트가 잘리며 모델이 시스템 프롬프트를 잃고 응답이 깨지는 문제를 확인함 → `app/modules/exam/llm.py`의 `ChatOllama`에 `num_ctx=16384` 명시로 수정. 동일 passage 재현 테스트로 확인(4096: 0/5문항 → 16384: 5/5문항). RunPod(vLLM)는 `max_model_len` 미지정 시 모델 네이티브 값을 쓰므로 로컬 개발 환경에만 있던 격차로 추정.

---

## 6. 보안 · 개인정보 (Claude Code는 반드시 준수)

- 개인정보 **마스킹은 입력 단계**에서, 외부/모델 호출 전에 수행
- 사용자 입력(생기부 메모·교사가 붙여넣은 예시 문제)은 **비저장 처리** — 영구 저장은 공개 코퍼스뿐
- **로그·캐시에 PII 금지**
- LangSmith 트레이싱: 생기부 모듈은 API 서버에서도 예외 없이 비활성(구조적으로 안 걸림, `record/chain.py`가 쓰는 백엔드가 LangChain Runnable이 아님). 출제 모듈은 2026-07-24부터 예외 — PII 마스킹 후 프로덕션에도 적용 가능(`LANGCHAIN_TRACING_V2=true` 옵트인, 기본값은 false). 합성 데이터 평가 스크립트(`evals/`)는 원래대로 선택적 사용. 자세한 내용은 LANGSMITH_GUIDE.md 1절
- 브라우저 요청은 Next.js 서버가 프록시하며, FastAPI는 `BUNPIL_API_KEY` 서버 간 인증을 요구
- 실데이터 미사용, 전부 합성
- ChromaDB **영구 컬렉션은 공개 자료(규정·성취기준)만**. 교사가 붙여넣은 예시 문제(`passage_text`)는 ChromaDB에 전혀 적재되지 않고 요청 처리 중 프롬프트에만 사용된 후 폐기. 학생 개인정보는 어디에도 미적재
- 생기부 출력에 "교사 최종 책임(보조수단)" 고지 표시
- **⚠️ 2026-07-23부터**: `JUDGE_BACKEND=openai`(기본값)에서는 문항 세트 생성마다 `passage_text`가
  구조 유사도 채점을 위해 OpenAI(gpt-5.6-luna)로 전송된다. PII 마스킹은 이 호출 이전에 이미
  적용돼 있으나(`_build_spec`이 그래프 진입 전에 마스킹), **저작권 있는 교사 지문 자체는 마스킹
  대상이 아니라 그대로 외부에 전송됨** — 의도적으로 수용한 트레이드오프(생성·Judge 모델 분리
  우선). 로컬로만 처리하려면 `JUDGE_BACKEND=local`로 전환할 것

---

## 7. 배포 (확정: AWS EC2(앱) + RunPod 서버리스(GPU))

**구성: 앱은 AWS EC2 상시 가동, GPU 추론만 RunPod 서버리스**

```
브라우저 ─→ [앱] AWS EC2 (t3.medium, CPU)           ─→ [GPU] RunPod Serverless
              FastAPI + Agent + ChromaDB                   Qwen2.5 14B(AWQ) / vLLM
              · PII 마스킹 후 추론 호출                     · 쓸 때만 과금, 유휴 시 0
              · ChromaDB는 EBS 볼륨에 저장                  · 콜드스타트 수초~수십초
```

- **앱 = AWS EC2** (t3.medium, ~4GB): FastAPI·agent·ChromaDB 구동 (UI는 Next.js, `frontend/`). ChromaDB는 EBS 볼륨에 영구 저장. IAM·보안그룹·SSH·Docker 표준 배포 절차를 따른다.
- **GPU = RunPod Serverless**: 추론만 요청당 과금, 유휴 시 0. 비싼 GPU 비용만 pay-per-use.
- **HTTPS**: Caddy 리버스 프록시로 자동 발급(+도메인) → 표준 배포 실습 포함.
- 요청 흐름: 브라우저 → EC2(마스킹·오케스트레이션) → RunPod 서버리스 호출 → 응답. 앱 로직 stateless, Chroma만 EBS 영구.
- **billing alarm 필수**: EC2 종량제라 예산 알람 설정. t3는 CPU burst throttle 있으니 임베딩 인덱싱은 한 번에 몰아서.
- **에이전트×서버리스 주의**: 출제 ReAct는 한 요청에 LLM을 여러 번 호출 → 첫 호출만 콜드스타트, 세션 중 워커 warm 유지로 후속 호출은 빠름. 긴 세션은 GPU 워밍 고려.

**운영비 (1인 사용 추정 / 월)**

| 항목 | 비용 |
|---|---|
| EC2 t3.medium (상시) | ~$30 |
| RunPod 서버리스 GPU (추론) | ~$1–5 |
| 스토리지(EBS·볼륨 수 GB) | ~$1 |
| **합계** | **~$32–36** |

- 데모·개발 단계는 EC2를 필요할 때만 켜서 더 절감 가능.
- 비용을 더 낮추려면 앱을 Lightsail($5~12 정액)이나 저가 VPS로 이전 가능(단 AWS 학습가치 ↓). 컨테이너화돼 있어 이전은 소규모.

**배포 실습 사다리**: Docker 이미지화 → docker-compose(로컬 검증) → EC2 배포(SSH·보안그룹) → Caddy 리버스 프록시·HTTPS → GitHub Actions 경량 CI(완료, 9절 참고)

---

## 8. 빌드 순서 (MVP)

1. **출제 모듈** — 데이터 부담 0(교사가 예시 문제 붙여넣기), RAG·Judge·Recall@5 바로 적용 → 가장 빠른 데모
2. **생기부 모듈** — 규정 RAG + 마스킹 + 사실보존 검증
3. **배포** — AWS EC2(앱) + RunPod 서버리스(GPU)

**Claude Code 활용 가이드**: 보일러플레이트(스캐폴딩·Docker·UI·글루)는 위임, 배포 단계(EC2·보안그룹·SSH·Caddy HTTPS·RunPod 서버리스 설정)는 학습 목적상 단계 설명 들으며 진행. 본 스펙을 컨텍스트로 제공할 것.

---

## 9. 결정 완료 / 남은 선택

**확정**
- 호스팅: **AWS EC2(앱) + RunPod 서버리스(GPU)**
- 생성·Judge 모델 선정 및 런타임 분리 아키텍처(2026-07-23): 결론과 실측 데이터는 **MODEL_SELECTION.md**
  1·2절, 배경 서사는 `bunpil_roadmap.md` 참고 — 여기서 반복하지 않음
- 운영비: 월 ~$32–36 (1인 기준, RunPod min workers=0 가정)
- **GitHub Actions CI** (2026-07-14 결정·구현 완료): 코드 회귀 확인용 **경량 CI만 도입**, LLM
  eval 자동화는 도입하지 않기로 결정
  - **경량 CI** (`.github/workflows/ci.yml`): 매 push/PR 블로킹. 백엔드 import 스모크테스트 +
    순수 로직 유닛테스트(`tests/`, `mask_pii`·`_rule_violations`) + 프론트 lint/build. 모델 호출
    없음(무료·수 분 내 완료)
  - **eval 자동화를 뺀 이유**: `JUDGE_BACKEND`로 Ollama/OpenAI Judge를 선택할 수 있지만,
    기존 EVAL.md 추세는 로컬 Ollama 기준이고 GitHub Actions 러너에는 해당 모델 인프라가 없다.
    다른 Judge로 CI 점수를 만들면 비교 기준이 달라지며, 생성·채점 결과의 변동성도 자동 블로킹
    게이트에 적합하지 않다. 이 규모에서는 로컬 수동 실행 + EVAL.md 기록을 유지한다.
    `evals/eval_exam.py`/`evals/eval_record.py`/`evals/eval_ragas.py`는 변경 없이 로컬 실행 스크립트로 유지
  - self-hosted runner(로컬 Ollama 호출)도 인프라 유지 부담 대비 이득이 적어 검토 후 제외

**나중에 정해도 되는 것**
- 비용 절감 시 앱을 Lightsail/저가 VPS로 이전 (AWS 학습가치 ↓)
