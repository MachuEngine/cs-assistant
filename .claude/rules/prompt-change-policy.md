# Prompt Change Policy

- 프롬프트(`prompts/`) 변경은 반드시 코드 변경과 분리된 **단독 커밋**으로 만든다.
  섞으면 eval 점수 변화의 원인(코드 vs 프롬프트)을 분리할 수 없다.
- Judge 루브릭(`prompts/judge_*.md`) 변경도 동일하게 단독 커밋.
- 프롬프트를 코드에 인라인하지 않는다. 전부 `prompts/`에서 로드한다.
