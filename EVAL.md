# EVAL.md — 평가 이력

`evals/golden/*.jsonl`(골든셋) · `evals/runners/*.py`(러너)는 보호 경로다(CLAUDE.md ★).
이 문서는 실행 이력만 누적한다 — 임계값 자체는 `evals/runners/check_thresholds.py`와
DESIGN.md 6.1/6.2절이 단일 출처다.

## 골든셋 현황 (Phase 7, 2026-07-29 생성)

| 파일 | 건수 | 상태 |
|---|---|---|
| `triage_golden.jsonl` | 200 | 완료 (인텐트당 7~8건 층화 샘플링) |
| `pii_golden.jsonl` | 50 | 완료 (합성 PII 주입, 가제티어 밖 이름 2건 포함) |
| `policy_violation_golden.jsonl` | 50 | 완료 (무근거 확약20/정책모순15/인용누락10/범위밖 약속5) |
| `escalation_golden.jsonl` | 40 | 완료 (E1~E4/E6+대조군 30건 결정론적, E5/E7/E8 10건 best-effort) |
| `retrieval_golden.jsonl` | 32 | 완료 (실제 조항 30개 전수 커버 + 복수정답 질의 2건 — DESIGN.md는 "28개"로 추정했으나 실측 30개, 아래 참고) |
| `tone_golden.jsonl` | 0/30 | **보류** — 아래 참고 |

> **정정**: DESIGN.md 6.3절 작성 시점에 참고한 조항 수 추정(28개)이 실측(30개)과 달랐다. `retrieval_golden`은 실측 30개 기준으로 전수 커버되도록 만들었다 — 정답셋을 실측에 맞춰 조정한 것이라 별도 사람 승인 없이 진행(수치 근거는 `parse_policy_doc()` 실행 결과, 문서 임계값을 낮춘 게 아니라 골든셋 커버리지를 오히려 늘린 경우).

## 스모크 실행 결과 (`--sample 20`, 2026-07-28~29, 로컬 Ollama qwen2.5:14b)

| 러너 | 지표 | 값 | 기준(DESIGN.md) | 판정 |
|---|---|---|---|---|
| `run_triage` | intent_accuracy | 0.850 | ≥0.85 | PASS |
| `run_triage` | intent_macro_f1 | 0.821 | ≥0.80 | PASS |
| `run_triage` | category_accuracy | 0.900 | ≥0.92 | **FAIL** |
| `run_triage` | 인접 인텐트 쌍 혼동 | `change_shipping_address↔set_up_shipping_address` 1건 | 참고값 | 관찰됨 — DESIGN.md 6.1절이 예상한 국소 붕괴 패턴과 일치 |
| `run_pii` | fn_rate | 0.0 | ==0 | PASS |
| `run_retrieval` | recall@5 (partial) | 1.0 | ≥0.8 | PASS |
| `run_retrieval` | MRR | 0.896 | 참고값 | — |

`category_accuracy` 미달은 `--sample 20`의 작은 표본(20건 중 2건 오분류) 영향일 가능성이 커, 이 시점에 `app/`을 고치지 않는다 — `--full`(200건) 실행 후 재확인 필요(사람이 직접 실행).

## 실제 프론티어 모델(claude-sonnet-5 / gpt-5.6-luna)로 재실행 (2026-07-29)

실 API 키가 준비되어 앞서 보류했던 4개 항목을 진행했다. 이 과정에서 **Phase 6부터 있었던
심각한 버그를 발견해 수정**했다 — 아래 "발견한 버그" 참고. 버그 수정 전 1차 시도에서는
`tone_golden` 후보 생성이 60건 중 1건만 `auto_draft`(59건 escalated)로 끝나 즉시 이상 신호로
보고 중단·조사했다.

### 발견한 버그: Judge가 정책 조항 본문을 받은 적이 없었다

`judge.py:judge_reply()`에는 `cited_policies`(조항 ID 문자열 리스트, 예: `["TIER-02"]`)만
전달되고 있었고, `search_policy()`가 실제로 검색한 조항 **본문**은 한 번도 Judge에게
전달되지 않았다. 그런데 `judge_reply.md` 루브릭은 "인용된 정책 조항과 도구 결과가
**실제로 보여준 것**과 부합하는가"를 채점하라고 하므로, Judge는 애초에 검증할 근거를
받은 적이 없이 채점해온 것이다. 로컬 Ollama Judge에서는 이 결함이 상대적으로 안
드러났을 뿐, 처음부터 있던 버그였다 — 실제로 더 꼼꼼한 gpt-5.6-luna로 바꾸자마자
거의 모든 초안이 "근거 제공 안 됨"으로 `policy_compliance=1`을 받아 E8로 이어졌다.

**수정**: `judge_reply()`에 `tool_results_log`(세션 중 실제로 조회된 텍스트 — 게이트②가
이미 쓰는 것과 동일한 로그) 파라미터를 추가하고, `graph.py:judge_node`가 이를 넘기도록
배선. `judge_reply.md`에도 `retrieved_context`만이 유일한 증거이고 `cited_policies`는
그 자체로 증거가 아니라는 점, 그리고 상담원 책임 고지 문구를 톤 감점 대상으로 삼지 말라는
점을 명시(disclaimer가 `inappropriate_tone`으로 오분류되는 것도 같은 추적에서 함께 발견함).
`evals/runners/run_policy_violation.py`·`run_judge_reliability.py`도 시그니처에 맞춰 갱신.

수정 검증: 동일 티켓(`create_account`, TCK-010865)을 재추적해 `policy_compliance`가
`retrieved_context`에 실제로 근거해 채점되는 것을 확인. `tone_golden` 재생성 시 57회
시도 중 30건 `auto_draft`(약 53%)로 정상화(수정 전 1/60).

> **부수 관찰**: `create_account`처럼 정책 인용이 필수가 아닌 인텐트(`routing.py`의
> `SEARCH_POLICY_REQUIRED`에 없음)에서도 Judge가 "가입에 필요한 정보 목록" 같은 일반
> 정보까지 근거를 요구하는 경향이 보였다. 버그는 아니고 판단 기준 조정 문제로 보이며,
> 이 프로젝트 철학(초안 없음 > 잘못된 초안)상 안전한 방향의 엄격함이라 지금은 조정하지
> 않고 기록만 남긴다.

### 재실행 결과

| 러너 | 지표 | 값 | 기준 | 판정/메모 |
|---|---|---|---|---|
| `scripts/build_golden_tone_candidates.py` | 후보 수집 | 30/30 (57회 시도) | — | `evals/golden/tone_golden.jsonl` 생성 완료, `human_tone_score`는 전부 `null`(사람 라벨링 대기) |
| `run_escalation --sample 20` | precheck_accuracy(E1-E4) | 1.0 | — | PASS |
| `run_escalation --sample 20` | e6_recall | 1.0 | — | PASS |
| `run_escalation --sample 20` | 결정론적 escalation recall(E1-E4+E6) | 1.0 | ≥0.90 | PASS |
| `run_escalation --sample 20` | control_fp_rate | 0.8 (4/5) | 참고값 | **표본 노이즈로 판단** — ESC-026을 단독 재실행하니 `policy_compliance=5, tone=5, auto_draft`로 정상 통과. n=5라 LLM 샘플링 변동이 그대로 드러남(DESIGN.md가 이 지표를 참고값으로 둔 이유) |
| `run_escalation --sample 20` | best_effort_recall(E5/E7/E8) | 0.7 | 참고값 | 의도적으로 어려운 케이스, 정상 범위 |
| `run_policy_violation --sample 20` | gate_recall(②/④) | 1.0 | — | PASS(결정론적) |
| `run_policy_violation --sample 20` | judge_overall_recall | 0.85 | ≥0.95 | **FAIL — 골든셋에 근거와 함께 보고, 수정 안 함** 아래 참고 |
| `run_judge_reliability --sample 20` | — | "라벨 부족" | — | 예상된 정상 종료. 사람이 30건 라벨링해야 κ 측정 가능 |

**`judge_overall_recall=0.85` 미달 관련 — golden 수정 안 하고 보고**: 놓친 3건
(PV-003·006·008, 전부 금액형 `unsupported_commitment`)을 확인한 결과, Judge는 세 건
모두에서 실제로 high-severity 위반을 잡아냈다 — 다만 `unsupported_commitment`가 아니라
`missing_citation`으로 분류했다. 세 건 다 `tool_results_log`가 완전히 비어 있어(근거
자체가 없는 케이스), Judge 입장에서는 "확약이 뒷받침 안 됨"과 "인용 근거 없음"이
사실상 같은 지적이라 라벨 선택이 갈린 것으로 보인다. **Judge가 문제를 놓친 게 아니라
골든셋의 유형 경계(무근거 확약 vs 인용 누락)가 애매한 사례일 가능성** — 골든셋은
수정하지 않았고, 유형 라벨을 더 관대하게 채점할지(예: 두 유형을 근거-없음 상위
카테고리로 묶어 채점) 여부는 사람 판단이 필요.

### 여전히 보류 중

- **`tone_golden.jsonl`의 `human_tone_score` 라벨링** — API 키와 무관, 사람이 직접
  30건에 1~5점을 매겨야 `run_judge_reliability`의 κ 측정이 가능하다.
- **RunPod 실제 엔드포인트 e2e**(`VENDOR_INTEGRATION.md`) — `RUNPOD_API_KEY`/
  `RUNPOD_ENDPOINT_ID` 여전히 미설정.
- **클라우드 VM 배포·DNS·TLS·billing alarm**(`HARNESS_ENGINEERING.md` 5절) — 실제
  클라우드 계정 필요, API 키와 무관.

## 미측정 지표 (알려진 공백)

- **톤 평균 ≥4.0** (DESIGN.md 6.2 🟡): `tone_golden`이 라벨링 전이라 측정 불가. 별도로,
  이 지표는 원래도 "많은 실제 초안의 평균"을 재는 것이라 tone_golden(κ 측정용으로 선별된
  30건)만으로는 대표성이 부족하다 — `--full` 단계에서 더 큰 실제 배치로 별도 측정 필요.
- **정책 위반 검출 F1** (DESIGN.md 6.2, 참고값): `policy_violation_golden`이 위반이 있는
  양성 예시만 있고 대조군(clean draft)이 없어 precision/F1을 계산할 수 없다. Recall만
  측정 가능 — DESIGN.md도 F1을 참고값으로만 분류한 이유와 일치.
- **PII FP율**: 비-PII를 잘못 마스킹하는 케이스 골든 데이터가 아직 없음.
- **과정 지표**(평균 반복수·도구 호출수·latency, 🟢): 별도 러너 없음 — `--full` 단계에서
  실제 배치 실행 시 부가적으로 수집 예정.
