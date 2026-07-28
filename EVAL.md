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

## 보류: 로컬 라이브 실행이 오래 걸리는 4개 항목 (2026-07-29)

다음은 전부 `run_reply()`(멀티턴 에이전트 루프) 또는 `judge_reply()`를 실제로 호출해야 하는
항목이라, 이 개발 환경(로컬 Ollama, 실제 벤더 키 없음)에서 스모크 20건조차 수십 분 이상
걸릴 수 있다(특히 `run_escalation`의 E5/E7/E8 best-effort 케이스는 의도적으로 예산·턴을
소진하도록 설계돼 있어 더 오래 걸림). 실행 자체가 실패한 건 아니다 — 처음엔 멈춘 것으로
오판해 kill했으나, 같은 케이스를 pytest로 단독 실행하면 12초 만에 끝나는 것을 확인해
파이프라인 자체는 정상임을 검증했다(자세한 경위는 `.claude/agent-memory/dev/MEMORY.md`).

- `scripts/build_golden_tone_candidates.py` (tone_golden 후보 생성)
- `evals/runners/run_escalation.py --sample 20`
- `evals/runners/run_policy_violation.py --sample 20`
- `evals/runners/run_judge_reliability.py` (위 tone_golden 후보 생성에 종속)

**사용자 결정**: 실제 프론티어 모델(생성 `claude-sonnet-5` / Judge `gpt-5.6-luna`) API 키가
준비되면 그때 이 4개를 실행한다. 모델 비교를 하게 되면 오픈소스(Ollama) 모델도 비교
대상으로 함께 돌려본다. Phase 8(RunPod 어댑터)·9(UI)·10(배포)는 이 4개 항목에 구조적으로
의존하지 않음을 PROMPTS.md 기준으로 확인함 — 다음 Phase 진행에 지장 없음.

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
