# Eval Integrity

- eval이 실패하면 `evals/golden/`이 아니라 `app/`을 고친다.
- 정답셋(`evals/golden/`)이 틀렸다고 판단되면 수정하지 말고 근거와 함께 사람에게 보고한다.
- `evals/runners/check_thresholds.py`의 임계값을 낮춰서 통과시키지 않는다.
- 개발 루프에서는 `--sample 20` 스모크셋만 실행한다. 전체(`--full`)는 사람이 직접 실행한다.
- 런타임 judge(`app/modules/reply/judge.py`)와 오프라인 eval은 반드시 같은 함수를 호출해야 한다.
  두 경로가 갈라지면 "검증한 것"과 "배포된 것"이 달라진다(분필에서 실제로 발생한 문제).
- Bitext의 `response` 컬럼은 정답셋으로 쓰지 않는다 — 우리 정책 문서에 근거하지 않는
  범용 템플릿이다.
