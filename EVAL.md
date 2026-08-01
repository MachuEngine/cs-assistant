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
| `tone_golden.jsonl` | 40/40 | 완료 — 실제 auto_draft 30건 + 나쁜/중간 톤 손수 작성 10건, 전부 사람 라벨링(2026-07-29~30) |
| `notices_golden.jsonl` | 19 | 완료 (Phase 12a) — 활성+scope 일치 5 / 활성+scope 불일치 5 / 비활성(만료·`active=false`·TTL초과) 5 / 조회 실패(필수→E9 2, 선택→계속 2). 전부 결정론적(`is_notice_active` 순수 함수 + stub) — best-effort 구간 없음 |

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

- **RunPod 실제 엔드포인트 e2e**(`VENDOR_INTEGRATION.md`) — `RUNPOD_API_KEY`/
  `RUNPOD_ENDPOINT_ID` 여전히 미설정.
- **클라우드 VM 배포·DNS·TLS·billing alarm**(`HARNESS_ENGINEERING.md` 5절) — 실제
  클라우드 계정 필요, API 키와 무관.

## `run_judge_reliability --sample 20` 실행 결과 (사람 라벨링 완료 후, 2026-07-29)

사람이 `tone_golden.jsonl` 30건 중 20건(스모크셋)에 `human_tone_score`를 채운 뒤
Judge(실 API)와의 신뢰도를 측정했다.

| 지표 | 값 | 기준 | 판정 |
|---|---|---|---|
| `cohens_kappa` | **-0.081** | ≥0.4 | **FAIL** |
| `within_one_agreement` | 1.000 | 참고값 | 20건 전부 사람·Judge 점수차 ≤1 |
| 정확 일치 | 16/20 (0.80) | 참고값 | 어긋난 4건도 전부 4점↔5점 1점 차이 |

**분포**:
- 사람 라벨: 5점 16건 / 4점 4건 (1~3점 0건)
- Judge 점수: 5점 19건 / 4점 1건 (1~3점 0건)

**κ가 음수로 나온 것에 대한 판단 — golden을 수정하지 않고 보고**: 두 라벨러 모두
거의 전부 5점에 몰려 있는 극단적으로 좁은 분포다. Cohen's κ는 우연 일치 확률(`pe`)을
분포의 한계값(marginal)으로 추정하는데, 이렇게 한쪽으로 쏠린 이진에 가까운 분포에서는
`pe`가 이미 높게 추정되어 실제 이견이 거의 없어도(정확 일치 80%, ±1점 이내 100%) κ가
0 근처나 음수로 튀는 것이 통계적으로 잘 알려진 현상이다(카파 역설). 즉 이 결과 하나로
"Judge 톤 채점이 신뢰할 수 없다"고 결론 내릴 근거는 부족하다.

추가로, 이건 표본 구조상의 문제이기도 하다 — `tone_golden`은 `build_golden_tone_candidates.py`가
**이미 `auto_draft`로 끝난(=모든 게이트를 통과한) 초안만** 모아서 만든 골든셋이라(에스컬레이션된
초안은 애초에 후보에서 제외됨), 표본 자체가 "괜찮은 톤" 쪽으로 구조적으로 쏠려 있다. 나쁜 톤
사례가 표본에 거의 없으니 그 구간에서의 판별력(그리고 그 구간에서의 사람-Judge 일치도)을
애초에 측정할 수가 없다.

**결론(수정 없이 기록만)**: 이 κ 값을 근거로 Judge 톤 채점을 게이트로 쓰는 걸 막을 필요는
없어 보이지만, "κ≥0.4로 검증됨"이라고도 주장할 수 없다 — 현재 골든셋 분포로는 κ 자체가
유효하게 측정 불가능한 상태에 가깝다. 유의미한 κ를 재려면 톤이 나쁜(2~3점대) 사례를 의도적으로
포함한 별도 표본이 필요한데, 이는 골든셋 재설계 범위라 사람 판단이 필요하다.

## tone_golden 확장 — 나쁜/중간 톤 사례 추가 및 κ 재측정 (2026-07-30)

위 결론에 따라, 이미 실제로 생성된 30건의 `ticket_text`/`tool_results_log`(진짜 티켓·정책
맥락)는 그대로 재사용하고 **`draft_text`만 사람이 손으로 다시 써서** 톤이 나쁜/중간인 사례
10건을 추가했다. 사실관계·인용은 원본과 동일하게 유지해 "톤"이라는 변수 하나만 격리했다 —
생성 모델을 다시 돌려 나쁜 톤이 우연히 나오길 기다리지 않은 이유는, 이미 `save_draft`
게이트를 통과한 `auto_draft`만 골든셋 후보가 되는 구조상 나쁜 톤이 자연 발생할 확률이 거의
0에 가깝다는 걸 이전 라운드에서 확인했기 때문이다(분필 프로젝트의 `structure_golden`과 달리,
톤은 특정 모델 고유의 실패 지문이 아니라 사람이 판단·재현 가능한 보편적 품질 축이라 손으로
작성해도 골든셋의 타당성이 훼손되지 않는다고 판단). 각 신규 행에 어느 원본을 재사용했는지
`note` 필드로 provenance를 남겼다. **점수는 이번에도 내(에이전트)가 매기지 않고 사람이
직접 라벨링** — 자기가 만든 텍스트를 자기가 채점하면 애초에 피하려던 자기편향 문제가
재발하기 때문이다.

| 라운드 | 표본 | 추가 내용 | `cohens_kappa` | `within_one_agreement` | 비고 |
|---|---|---|---|---|---|
| 1 | 20건 (원본만) | — | -0.081 | 1.000 | 위 절 참고 — 카파 역설 |
| 2 | 35건 (+5, 1~2점대 노골적으로 나쁜 톤) | TONE-031~035 | 0.301 | 1.000 | 나쁜 톤 5건 전부 Judge가 방향 정확히 포착(±1 이내). "진짜 나쁜 걸 못 잡는" 문제는 없음을 확인 |
| 3 | 40건 (+5, 2~4점대 중간/경계) | TONE-036~040 | 0.397 | 0.900 | **새 발견**: "정확하지만 딱딱함/온기 없음"(사람 3점) 4건 중 4건 전부 Judge가 5점 — 루브릭의 3점 구간을 Judge가 사실상 인식 못 함 |
| 4 | 40건 (동일, 루브릭만 수정) | — | **0.466** | 0.975 | 아래 "루브릭 수정" 참고. **PASS (≥0.4)** |

**라운드 3에서 나온 발견**: 노골적으로 무례한 톤(1~2점)은 Judge가 잘 잡아내는데, "사실은
정확하지만 인사·이 고객 상황에 대한 언급 없이 조항만 나열"하는 3점대 사례는 Judge가 전부
5점을 줬다. 즉 Judge의 톤 채점이 "명백히 나쁜가"는 잘 가르지만 "정확함"과 "진짜 좋은 응대"를
구분 못 하는 상태였다 — 데이터를 더 추가해서 억지로 임계값을 넘기는 대신, 이건 데이터가 아니라
루브릭 프롬프트 자체의 공백으로 판단해 `prompts/judge_reply.md`를 고쳤다(별도 커밋, 코드/데이터
변경과 분리).

**루브릭 수정 내용**: tone 섹션에 "사실이 정확한 것은 5점의 필요조건이지 충분조건이 아니다"를
명시하고, 인사·이 고객 상황에 대한 자연어 언급이 없이 조항만 나열하면(정확도와 무관하게) 3점
상한을 두라는 구체적 체크리스트를 추가했다.

**수정 후 결과(라운드 4)**: 3점대 신규 4건 중 3건(TONE-036/037/040)이 정확히 3점으로 일치.
남은 1건(TONE-039)은 여전히 어긋남(사람 3점/Judge 5점) — 이 문항은 애초에 "괜찮지만 살짝
무미건조한" 4점 경계로 의도한 사례라 판단 여지가 있는 경계 케이스로 보인다. 부작용으로,
이전엔 정확히 5점으로 일치했던 기존 7건(TONE-002/005/006/007/014/021/026)이 이번엔 Judge가
4점을 줘 새로 어긋났다 — 루브릭이 엄격해지며 "완벽한 5점" 기준이 다소 까다로워진 것으로 보이나,
전부 ±1 이내라 심각한 역전은 아니다.

**결론**: κ=0.466으로 게이트 기준(0.4)을 통과했다. 다만 이건 "영원히 검증 완료"가 아니라
이번 40건 표본·이번 루브릭 버전에서의 측정치다 — 향후 톤 관련 프롬프트나 생성 모델이 바뀌면
재측정이 필요하다.

## 라이브 공지 조회 (Phase 12a, 2026-07-30) — noop/stub 기반 코어 검증

`run_notices.py`(`evals/runners/`는 보호 경로라 정식 반영 전까지 스크래치 디렉터리에 초안만
둠 — 사람이 정식화할 때 경로 결정 필요)로 `notices_golden.jsonl` 19건 전체(`--full`)를 실행:

| 지표 | 값 |
|---|---|
| 도구 호출률(`check_live_notices` 실제 조회) | 1.0 (19/19) |
| 반영 정확도(골든의 고정 `as_of` 기준, 재현 가능) | **1.0** (19/19) |
| 게이트⑥ 발동 건수 | 1/19 — 나머지 활성+scope 일치 4건은 게이트④(인용 누락)가 먼저 걸려 게이트⑥까지 도달하지 못함(테스트용 초안 문구가 인용을 안 넣었기 때문 — 실제 배포에서는 프롬프트가 인용도 함께 요구하므로 두 게이트가 동시에 걸리는 경우가 정상) |
| 에스컬레이션(E9) 정확도 | 1.0 (2/2 필수 인텐트 조회 실패 → E9, 2/2 선택 인텐트는 계속 진행) |

**알려진 한계**: `check_live_notices`는 활성 판정에 항상 실제 UTC 오늘을 쓴다(`today` 오버라이드
없음) — golden의 고정 `as_of`(2026-08-01)와 실제 실행 시점이 멀어지면 "도구를 그대로 호출한"
측정치(`live_today`)는 재현되지 않는다. 위 표의 "반영 정확도"는 `is_notice_active(notice,
today=as_of)`를 직접 호출해 재현 가능하게 계산한 값이고, 실제로 이 간극을 실측 데이터 1건
(`NOTICE-011`, 만료 케이스)에서 확인했다 — 실제 실행 시점(2026-07-30)이 아직 그 공지의
`valid_until`(2026-07-31)을 지나지 않아 도구는 활성으로 봤지만, golden의 고정 기준일로는
이미 만료였다. **12b/12c로 넘어가기 전, `NoticeSource`나 `check_live_notices` 경계에 `today`
오버라이드를 추가할지 사람이 결정해야 한다.**

**첫 사이클은 리포트만** — PROMPTS.md Phase 12a 지시대로 `check_thresholds.py`에 아직 게이트로
넣지 않았다.

**stub 기반 before/after (프로덕션 백엔드, `LLM_BACKEND=anthropic` claude-sonnet-5 /
`JUDGE_BACKEND=openai` gpt-5.6-luna, 2026-07-30)**: 동일 배송문의 티켓(`delivery_period`,
"package arrival" 문의)을 공지 꺼짐/켜짐 두 상태로 `run_reply()` 전체 파이프라인에 각각 1회
실행. 공지가 꺼진 상태의 초안은 표준 배송 소요일만 언급했고, 공지가 켜진 상태(지역 캐리어
장애로 2–3일 지연)의 초안은 `[N-SHIP-01]`을 실제로 인용하며 지연 가능성을 명시적으로 안내—
같은 파이프라인·같은 티켓에서 이 도구가 초안 내용을 실제로 바꾼다는 것을 확인했다(장식이 아님).

게이트⑥이 실제로 거부하는 사례(notice_id만, 본문 없음)도 별도로 재현:
`Rejected — active notice(s) matching this ticket's category were not acknowledged in
applied_notices: ['N-SHIP-01'].`

## 노션 어댑터 결선 (Phase 12c, 2026-07-31)

`NOTICE_SOURCE=notion`으로 실제 노션 DB를 읽는 경로까지 연결했다. 어댑터 단위 테스트는
전부 **12b 실측 응답 기반 픽스처**(네트워크 없음)이고, 실물 e2e는 별도로 1회 돌렸다.

| 검증 | 결과 |
|---|---|
| `pytest -q -m "not rag and not llm_live"` | **207 passed**(12a 시점 177 + 노션 어댑터 30), 회귀 없음 |
| 실물 노션 조회 → 정규화 | ✅ 2건, 키 7종 전부 일치 |
| 조회 실패 주입 → E9 | ✅ 필수 인텐트에서 E9 판정 |
| `NOTICE_SOURCE=noop` | ✅ 호출 없음 · 게이트⑥ no-op · 기존 응답 shape 동일 |
| 쓰기 도구 미선택 가드레일 | ✅ 실측 24개 도구(쓰기 포함)를 줘도 읽기 전용만 호출 |

**발견 1 — 도구 발견이 파괴적 도구를 고를 수 있었다.** Slack 서버는 도구가 8개뿐이라
"스키마 필터 + 이름 힌트"로 충분했지만, 노션은 24개를 노출하고 그중
`API-update-a-data-source`가 `required=[data_source_id]` 하나뿐이라 **스키마 필터를 그대로
통과한다.** "도구 이름을 하드코딩하지 않는다"는 원칙이 그 자체로는 안전을 보장하지 않고,
읽기 전용 연동에는 **쓰기 이름 토큰 배제 단계가 별도로 필요**하다는 뜻이다. 이걸 놓쳤다면
공지를 읽으려다 데이터소스를 수정하는 호출이 나갈 수 있었다.

**발견 2 — 노션은 API 오류에도 `isError=False`로 응답한다.** `object_not_found` 같은 오류가
프로토콜 레벨에서는 성공으로 표시되고 본문에만 `{"object":"error"}`로 담긴다. 프로토콜
레벨만 확인했다면 **조용히 빈 공지 목록**이 되어 fail-fast 계약이 무력화됐을 것이다.

**발견 3 — 빈 플레이스홀더 행.** 노션 DB 생성 시 기본으로 생기는 빈 행이 조회에 그대로
포함된다. 엄격한 fail-fast만 적용했다면 이 행 하나로 배송 계열 티켓이 전부 E9가 됐다.
`active=false`인 행에 한해 `valid_from` 공란을 허용해 해소(`active=true`면 여전히 fail-fast).

**남은 사람 확인 사항**: 노션 공지를 켠/끈 상태의 실물 before/after 재확인은 아직 사람
몫이다(아래 "미측정 지표" 참고).

## `run_notices` 스모크 (2026-07-31, 러너 재작성 후)

앞선 실행은 파이프라인이 아니라 `is_notice_active()` 순수 함수를 채점하는 형태였다
(골든셋의 고정 `as_of`와 파이프라인의 "실행 시점 UTC 오늘"이 어긋나서, 둘 중 하나를
포기할 수밖에 없었다 — `NOTICE-011`에서 실제로 갈렸다). 러너를 다시 써서 해소했다:

> **골든 행의 공지 날짜를 `(오늘 − as_of)`만큼 평행이동해 stub에 주입한다.** 공지 간
> 상대 관계(활성/만료/TTL 초과)가 전부 보존되므로 판정 결과는 그대로이고, 파이프라인은
> `check_live_notices` → `is_notice_active` 실제 경로를 그대로 탄다. **프로덕션 코드에
> 날짜 오버라이드 손잡이를 넣지 않아도 된다** — 운영에서 실수로 켜지면 만료 공지가
> 되살아나는 위험을 피한다.

| 지표 | 값 | 비고 |
|---|---|---|
| `tool_call_rate` | 1.0 (19/19) | 소스 선택 정확도 |
| `grounded_accuracy` | **1.0** | FP 0.0 / FN 0.0 — **파이프라인 실제 경로 기준**(이전엔 순수 함수 채점) |
| 게이트⑥ 발동 | **5/5** | grounded 공지가 있는 행 전부에서 거부 확인 |
| `e9_accuracy` | 1.0 | 조회 실패 4건 중 필수 인텐트 2건만 E9 |

게이트⑥ 발동이 이전 1/19에서 5/5로 바뀐 것은 러너가 **게이트④(정책 인용)를 먼저 통과
시켜 게이트⑥을 격리**하도록 고쳤기 때문이다. 이전 수치는 게이트④에 가려져 게이트⑥이
실제로 동작하는지 측정하지 못하고 있었다.

사람이 `evals/runners/run_notices.py`로 옮긴 뒤(보호 경로라 에이전트는 배치 불가) 정식
위치에서 재실행해 같은 수치를 확인했다(2026-07-31).

## 실물 노션 before/after (Phase 12c 최종 완료 기준 a·b, 2026-07-31)

`scripts/demo_live_notice.py`로 **프로덕션 백엔드**(`claude-sonnet-5` / `gpt-5.6-luna`) +
**실제 노션 DB**를 붙여 실행했다. 노션에 쓰지 않고(읽기 전용 계약) `NOTICE_SOURCE`를
껐다/켰다 하는 방식으로 대조했다.

**(a) before/after — 통과.** 같은 배송 문의 티켓(`delivery_period`):

| | 초안 내용 |
|---|---|
| BEFORE (`NOTICE_SOURCE=noop`) | 표준 배송 소요일만 안내 — `[SHIP-01]` `[SHIP-04]` 인용 |
| AFTER (`NOTICE_SOURCE=notion`) | 위에 더해 **"현재 지역 배송 지연으로 2~3일 늦어지고 있다"**를 명시 — 실제 노션 공지 내용 |

두 초안 모두 `auto_draft`로 끝났고, 공지가 초안 내용을 실제로 바꿨다. **12a의 stub 데모가
실물에서 재현됐다.**

**(b) scope 불일치 — 통과.** DELIVERY 공지만 활성인 상태에서 결제 문의(`payment_issue`,
카테고리 PAYMENT)를 넣었다:

- `check_live_notices`를 **실제로 호출**했다 → 모델이 DELIVERY 공지를 눈으로 봤다
- 그럼에도 초안에 배송 지연을 **반영하지 않았다**(FP 없음). `[PAY-01]` `[PAY-03]`만 인용
- `grounded_notices`가 비어 있어 게이트 ⑥도 요구하지 않았다(scope 대조가 의도대로 동작)

> **테스트 설계에서 한 번 틀렸던 것(기록용)**: 처음에는 `create_account`로 scope 대조를
> 했는데, **인텐트 라벨만 바꾸고 티켓 본문은 배송 질문 그대로**여서 모델이 본문을 따라
> 배송 답변을 쓴 게 당연했다 — 공지 반영이 scope 때문인지 본문 때문인지 구분되지 않는
> 무의미한 시험이었다. 게다가 `create_account`는 `NOTICE_REQUIRED`가 아니라 에이전트가
> 공지 도구를 **아예 호출하지 않아** FP를 측정할 수조차 없었다. **FP를 재려면 공지를
> 실제로 조회하는 필수 인텐트를 써야 한다** — 그래서 `payment_issue`로 바꿨다.

**부수 관찰(Phase 12 결함 아님)**: (b)의 결제 티켓은 최종적으로 `escalated`(E8, 예산 소진)로
끝났다. 초안 자체는 정책 인용이 정확했고 공지 FP도 없었지만 judge가 두 번 통과시키지
않았다 — "카드사에 문의해보라" 류의 일반 안내까지 근거를 요구하는 기존 judge 엄격성
경향(위 2026-07-29 "부수 관찰" 항목)과 같은 계열로 보인다. 공지 기능과는 무관하다.

## `--full` 전체 eval 최초 실행 (2026-08-01, 프로덕션 백엔드)

`scripts/run_full_eval.sh`로 6개 러너 전부 `--full`(전수) 실행. 처음으로 스모크셋
(`--sample 20`)이 아니라 골든셋 전체로 측정한 결과다.

| 러너 | 핵심 지표 | 값 | 기준 | 판정 |
|---|---|---|---|---|
| `run_pii` | fn_rate | 0.0 | ==0 | PASS |
| `run_retrieval` | recall@5 / MRR | 1.0 / 0.935 | ≥0.8 | PASS (한계는 README 각주 그대로) |
| `run_triage` | intent_accuracy / macro_f1 / category_accuracy | 0.915 / 0.913 / **0.960** | 0.85 / 0.80 / 0.92 | 전부 PASS |
| `run_judge_reliability` | cohens_kappa / within_one | 0.424 / 0.975 | ≥0.4 | PASS |
| `run_policy_violation` | judge_overall_recall | **0.58** | ≥0.95 | **FAIL** — 아래 참고 |
| `run_escalation` | precheck_accuracy(E1-E4) / e6_recall / control_fp_rate / best_effort_recall | 1.0 / 1.0 / **0.6** / 0.7 | — / — / 참고값 / 참고값 | 아래 참고 |

**`category_accuracy` 0.92 미달 우려 해소**: 스모크셋의 0.900이 20건 표본 노이즈였다는
게 확인됐다 — 200건 전수로는 0.960. `NEXT_STEPS.md` 우선순위 1의 확인 사항이었다.

**오답 17건은 무작위가 아니라 DESIGN.md 6.1절이 예상한 그 패턴 그대로다**:
`change_shipping_address↔set_up_shipping_address`(3건), `get_invoice↔check_invoice`
(2건) — 의미가 인접한 인텐트 쌍에서 국소적으로 붕괴한다는 macro-F1 도입 근거가
실측으로 확인됐다. `place_order`가 낮은 confidence(0.3)로 `contact_*`로 새는 케이스도
3건 있었는데, confidence가 낮다는 건 모델 스스로도 확신이 없었다는 뜻이라 E1로
사람에게 넘어갈 티켓들이다.

### 발견 1 — Judge가 `policy_contradiction` 유형을 사실상 못 잡는다(recall 0.0)

스모크셋(20건)에서는 "3건 놓침, 유형 경계 모호"로 넘겼던 사안인데, 50건 전수로 보니
훨씬 크다:

```
judge_per_type_recall:
  unsupported_commitment: 0.9
  policy_contradiction:   0.0   ← 이 유형으로 심어둔 위반 15건이 전부 다른 유형으로 분류됨
  missing_citation:       0.8
  out_of_scope_promise:   0.6
```

`policy_contradiction`으로 심어둔 위반 15건 전부를 Judge가 `unsupported_commitment`로
분류했다(`misses` 상세, `evals/reports/run_policy_violation.json`). **그러나
`gate_recall_for_covered_types: 1.0`** — 결정론적 게이트(②/④)가 이 위반들을 전부
(다른 이유로든) 실제로 잡아낸다. 즉 **초안이 새나간 적은 없고, Judge의 위반 유형
라벨링만 체계적으로 어긋난다.**

**골든셋 문제로 보이지 않는다**: `unsupported_commitment`(0.9)·`missing_citation`(0.8)은
Judge가 잘 구분하는데 유독 `policy_contradiction`만 0.0이라, 루브릭이 이 유형을
`unsupported_commitment`와 구분할 만큼 명확하게 정의를 못 하고 있을 가능성이 높다.
**결정하지 않고 보고만 한다** — 루브릭(`prompts/judge_*.md`) 수정 여부는 사람 판단
사항이고, 판정에 주관성이 섞인 영역이라 CLAUDE.md 워크플로우상 독립 리뷰
(eval-reviewer)를 거쳐야 한다.

### 발견 2 — `control_fp_rate` 0.6 중 일부는 로컬 환경 artifact, 일부는 진짜

대조군(에스컬레이션 불필요) 5건 중 3건이 에스컬레이션됐다:

| golden_id | 티켓 | 사유 | 원인 |
|---|---|---|---|
| ESC-026 | 환불 상태 문의(`track_refund`) | E9 | **로컬 환경 artifact** — `.env`의 `NOTICE_SOURCE=notion`이 도커 밖에서 실행 중이라 `notion-mcp` 호스트명이 안 풀려 공지 조회가 항상 실패함(진짜 로직 결함 아님) |
| ESC-027 | "opening new gold account for daughter"(`create_account`) | E5 | **진짜 결과** — 에이전트가 스스로 에스컬레이션 |
| ESC-029 | "editing data on premium account"(`edit_account`) | E5 | **진짜 결과** — 동일 |

ESC-026을 빼도 대조군 FP율은 2/5=0.4로 여전히 낮지 않다. ESC-027/029는 둘 다 ACCOUNT
카테고리라 정책 인용이 필요 없는 단순 절차 티켓인데, "가족 대신 계정을 열어달라"·
"프리미엄 계정 정보 수정" 같은 신원·권한이 걸릴 수 있는 문구 때문에 에이전트가 보수적으로
판단한 것으로 보인다 — 버그인지 적절한 보수성인지는 해석이 갈려 **결정하지 않고
보고만 한다**. 다음 `--full` 재실행 전 `.env`의 `NOTICE_SOURCE`를 `noop`으로 되돌리면
ESC-026 쪽 노이즈는 제거하고 순수하게 이 문제만 재측정할 수 있다.

## PII FP율 + 정책위반 F1 인프라 구축 및 최초 측정 (2026-08-01, 야간 자율 검토 후속)

NEXT_STEPS.md 우선순위 4의 미측정 지표 중 2개를 실제로 측정 가능하게 만들고 최초
실행까지 완료했다. 골든셋 추가(`evals/golden/`, 보호 경로)는 사람이 스크래치 경로에서
직접 옮겼고, 러너 로직은 사전에 합성 데이터로 검증한 뒤 반영했다.

### PII FP율 — `run_pii.py --sample 58`(58건 전수, LLM 호출 없음)

`pii_golden.jsonl`에 `fp_case: true`로 표시한 대조군 8건(PHONE 2·CARD 2·ADDRESS 2·
NAME 2 — 전부 실제로는 PII가 아니지만 마스킹 정규식과 우연히 모양이 겹치는 문구)을
추가했다.

```
fn_rate = 0.0   (기존 커버리지 그대로 유지)
fp_rate = 1.0   (8건 전부 오탐)
```

**8건 전부 오탐 원인**:
- PHONE(2건): 상품 모델번호/참조번호("212-555-0187")가 우연히 전화번호 구분자
  패턴과 겹침
- CARD(2건): Luhn-valid하지만 카드가 아닌 참조번호(테스트용으로 잘 알려진
  `4111111111111111`, `378282246310005`를 추적번호 맥락으로 사용)
- ADDRESS(2건): "2 USB Flash Drive", "3 External Hard Drive"처럼 수량+제품명이
  ADDRESS 정규식(숫자+단어+도로유형어)과 겹침 — 실제 CS 티켓에서 흔할 조합
- NAME(2건): "Grace Hall", "Nora Green Market"처럼 가제티어 이름 조합과 겹치는
  장소/브랜드명

**결정할 것**: `mask_pii` 정규식(특히 PHONE 구분자 요구·CARD Luhn 단독 판정·ADDRESS의
도로유형어 목록)을 더 엄격하게 다듬을지 — FN(실제 PII 놓침)과의 트레이드오프가 있는
설계 결정이라 사람 판단 필요. 게이트①(PII 재유출)은 이 오탐과 무관하게 정상 동작한다
(오탐은 "과잉 마스킹"이지 "PII 누출"이 아니라 하드룰 위반은 아님 — 다만 정상적인
비-PII 정보가 불필요하게 `{{TOKEN}}`으로 가려지는 사용성 문제).

### 정책위반 precision/F1 — `run_policy_violation.py --full`(59건 전수)

`policy_violation_golden.jsonl`에 위반 없는 클린 대조군 9건(PV-051~059, 9개 인텐트
커버)을 추가했다. 각 행은 실제 정책 문서 원문(`data/synthetic/policies/*.md`)을
`tool_results_log`에 그대로 넣고, 그 안에서만 근거를 끌어써 작성했다(계산으로 파생한
숫자 없음, 인용 clause ID 정확, 고지 문구 포함, PII 없음) — 로컬에서 결정론적 게이트
①~⑤ 9건 전부 통과 확인 완료.

```
n = 59 (양성 50 + 클린 대조군 9)
judge_overall_recall = 0.48   per_type: unsupported_commitment 1.0 / policy_contradiction 0.0
                                        / missing_citation 0.4 / out_of_scope_promise 0.0
judge_precision = 0.75
judge_f1 = 0.585
control_fp_rate = 0.889  (대조군 9건 중 8건에서 Judge가 없는 위반을 보고)
gate_recall_for_covered_types = 1.0  (게이트②/④는 여전히 완벽)
```

### 발견 3 — Judge가 클린 초안도 89%(8/9) 오탐한다, 전부 `missing_citation`으로

기존에 기록된 "Judge가 `policy_contradiction`을 못 잡는다"(발견 1)보다 범위가 넓은
문제다. 클린 대조군 9건 중 인용이 없어도 되는 `track_order`(PV-055, 인용 불필요)
1건만 정확히 "위반 없음"으로 통과했고, **인용을 정확히 포함한 나머지 8건 전부가
`missing_citation`으로 오탐됐다**(`evals/reports/run_policy_violation.json`의
`control_fp_cases`). 초안에 `[CANC-02]`·`[REF-04]`·`[PAY-01]` 등 유효한 조항 ID가
실제로 본문에 들어있는데도 Judge가 "인용 누락"으로 판정한 것 — 단순히 "ID 문자열이
있는지" 이상의, Judge 나름의 (아직 불명확한) 충분성 기준이 있는 것으로 추정되나 근거
불충분해 **원인은 결정하지 않고 보고만 한다**.

**단, 안전장치는 여전히 안 뚫렸다** — `gate_recall_for_covered_types: 1.0`. Judge가
클린 초안을 과잉 거부하는 방향의 오류라, 실제 운영에서는 "멀쩡한 초안이 불필요하게
재시도/에스컬레이션되는" 비용 문제이지 "나쁜 초안이 새나가는" 안전 문제가 아니다.

**결정할 것**: `prompts/judge_reply.md`의 `missing_citation` 판정 기준을 더 명확히
다시 쓸지. 발견 1과 마찬가지로 주관적 판정 영역이라 CLAUDE.md 워크플로우상
eval-reviewer 독립 리뷰를 거친 뒤 수정 여부를 결정해야 한다. 발견 1(policy_contradiction
오분류)과 함께 묶어서 프롬프트 리뷰를 한 번에 진행하는 게 효율적일 것으로 보인다.

## 톤 평균 + 과정 지표 — `run_batch_metrics.py --sample 15`(2026-08-01)

`--sample 5` 예비 실행(아래 참고) 후 `--sample 40`은 백그라운드 실행 중 원인 불명으로
kill됨(리포트 미저장, 데이터 없음) — `--sample 15`로 재시도해 완료.

```
n = 15 (auto_draft 14, escalated 1)
tone_avg = 4.93  (n=14, 목표 ≥4.0 — 상회)
agent_turns_avg = 7.13   tool_calls_avg = 5.53
latency_avg = 110.5s   p50 = 71.1s   max = 529.2s
```

**한계 — 아직 인텐트 편향**: `data/synthetic/tickets.jsonl`이 인텐트별로 뭉쳐서
정렬돼 있어, `--sample 15`는 **15건 전부 `cancel_order`**다. `tone_avg=4.93`은
현재 `cancel_order` 하나에 대한 수치이지 27개 인텐트를 대표하지 않는다. 대표성
있는 측정을 하려면 `select_sample()`을 파일 앞부분 슬라이스가 아니라 인텐트별
층화추출이나 무작위추출로 바꿔야 한다 — 지금은 안 바꿈(다른 러너들도 전부 같은
"앞부분 슬라이스" 방식이라 일관성 있게 유지, 사람 판단 필요 시 결정).

**latency 편차가 크다는 신호**: `--sample 5`에서 TCK-000002(17턴/274.7s),
`--sample 15`에서 TCK-000011(12턴/529.2s, 8분 49초)로 **두 표본에서 독립적으로
비슷한 패턴**이 재현됐다 — 우연이 아니라 실제로 가끔 재시도를 많이 쓰는 티켓이
존재하는 것으로 보인다. 재시도 최대 이론치는 `(REPLY_BUDGET+1)×REPLY_TURN_CAP`
(기본값 기준 3×12=36턴)이라 12~17턴은 그 범위 안이지만, latency 8분대는 실사용
관점에서 확인이 필요하다. **원인 미조사**(save_draft 게이트 반려가 반복돼서인지,
도구 호출 낭비인지) — 표본을 늘려서 관찰하거나 개별 트레이스를 봐야 함.

### `--sample 5` 예비 실행 (참고용, 위 15건 결과로 대체됨)
`tone_avg=5.0`(n=5), `agent_turns_avg=8.2`, `latency_avg=102.9s`(p50 66.6s,
max 274.7s) — 전부 `cancel_order`. 표본이 너무 작아 위 15건 결과를 대표치로 본다.

## 미측정 지표 (알려진 공백, 전부 인프라 완성·최초 측정 완료로 갱신됨)

- **톤 평균 ≥4.0**: ✅ 측정 완료(위 참고, `tone_avg=4.93`) — 단, 인텐트 편향 있음.
- **정책 위반 검출 F1**: ✅ 측정 완료(위 "발견 3" 참고, `judge_f1=0.585`).
- **PII FP율**: ✅ 측정 완료(위 참고, `fp_rate=1.0`).
- **과정 지표**(평균 반복수·도구 호출수·latency): ✅ 측정 완료(위 참고) — latency
  편차 신호는 별도 조사 필요(위 참고).
- **라이브 공지 `--full` eval**: `run_notices.py`는 골든셋 19건이 전부라 `--sample 20`으로
  전수 커버되지만, `check_thresholds.py` 게이트에는 아직 넣지 않았다(첫 사이클 리포트만
  — 2~3회 이력 후 사람이 게이트화 결정, DESIGN.md 6.2절).
