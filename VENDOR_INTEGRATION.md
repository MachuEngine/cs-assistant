# VENDOR_INTEGRATION.md — 벤더 연동 전략: 정식 지원 vs 커스텀 어댑터

Phase 8 산출물. 판단 기준·근거·구현 세부는 DESIGN.md 10절이 원 출처이며, 이 문서는
실제 구현 결과와 코드를 근거로 그 판단을 검증한다.

## 판단 기준표

| 기준 | 정식 지원으로 충분 | 커스텀 어댑터 필요 |
|---|---|---|
| LangChain 공식 패키지 존재 | 있음(`langchain-anthropic`, `langchain-openai`, `langchain-ollama`) | 없거나 표준과 다름 |
| API 계약 | 단일 동기 요청-응답 | **비동기 job queue**(제출 → job_id → 상태 폴링) |
| 이 프로젝트 적용 | Anthropic(생성 기본) · OpenAI(Judge 기본) · Ollama(로컬 개발) | RunPod 서버리스 |

## 왜 Anthropic/OpenAI/Ollama는 공식 클래스로 충분한가

세 벤더 모두 `POST` 한 번에 완성된 응답(또는 스트림)을 동기적으로 돌려준다.
`app/common/llm/backends/{anthropic,openai,ollama}.py`는 각각 `ChatAnthropic`/
`ChatOpenAI`/`ChatOllama`를 그대로 반환하거나 얇게 감싸기만 한다 — 메시지 변환,
tool_calls 파싱, 폴링 같은 걸 직접 구현할 필요가 없다. 예:

```python
# app/common/llm/backends/ollama.py
def get_chat_ollama(model: str | None = None, temperature: float = 0.7) -> ChatOllama:
    return ChatOllama(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        model=model or os.getenv("OLLAMA_MODEL", "qwen2.5:14b"),
        temperature=temperature,
    )
```

## 왜 RunPod은 커스텀 어댑터가 실제로 필요했는가

RunPod 서버리스 handler는 `POST /run`으로 job을 제출하면 `job_id`만 돌아오고,
실제 완료 여부·결과는 `GET /status/{job_id}`를 반복 조회해야 알 수 있다(GPU
콜드스타트·워커 오토스케일링 큐잉 때문에 구조적으로 비동기). 이 프로토콜에 맞는
공식 LangChain 통합이 없어 `BaseChatModel`을 직접 상속해야 했다.

### 1) 메시지 변환 양방향

`app/common/llm/backends/chat_runpod.py`의 `_to_runpod_messages()`가 LangChain
`BaseMessage`(`HumanMessage`/`AIMessage`/`ToolMessage`)를 RunPod handler가 받는
OpenAI 호환 dict로 바꾼다 — role 매핑(`human→user`, `ai→assistant`), `AIMessage.
tool_calls`를 OpenAI `tool_calls` 스키마(`function.arguments`는 dict가 아니라
JSON 문자열)로, `ToolMessage.tool_call_id`를 그대로 보존:

```python
def _to_runpod_messages(messages: list[BaseMessage]) -> list[dict]:
    result = []
    for m in messages:
        msg: dict = {"role": _ROLE_MAP.get(m.type, "user"), "content": m.content or ""}
        if isinstance(m, AIMessage) and m.tool_calls:
            msg["content"] = m.content or None
            msg["tool_calls"] = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc["args"], ensure_ascii=False)}}
                for tc in m.tool_calls
            ]
        if isinstance(m, ToolMessage):
            msg["tool_call_id"] = m.tool_call_id
        result.append(msg)
    return result
```

반대 방향은 `_build_ai_message()`가 담당 — handler 응답의 `tool_calls`(OpenAI
포맷, `arguments`가 JSON 문자열)를 LangChain `AIMessage.tool_calls`(dict `args`)로
되돌린다. 이 왕복 변환이 없으면 reply agent(`app/modules/reply/graph.py`)의
`response.tool_calls`가 비어버려 도구 호출 자체가 안 된다.

### 2) `bind_tools()` — LangChain 도구 → OpenAI 호환 tool schema

```python
def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
    from langchain_core.utils.function_calling import convert_to_openai_tool
    tool_defs = [convert_to_openai_tool(t) for t in tools]
    return self.bind(tools=tool_defs, **kwargs)
```

reply agent가 넘기는 8개 도구(`app/modules/reply/tools.py`의 `TOOLS`)가 그대로
이 경로를 통과해야 `LLM_BACKEND=runpod`로 전환해도 에이전트 루프가 동일하게
동작한다.

### 3) job_id 폴링 — 재제출 금지

```python
# app/common/llm/backends/runpod.py
except (httpx.ReadTimeout, httpx.TimeoutException) as exc:
    raise TimeoutError(
        "RunPod 작업 제출 응답을 받지 못했습니다. 재제출하지 않습니다."
    ) from exc
```

`/run` 제출 응답을 못 받았다고 다시 제출하면, 먼저 보낸 job이 서버 쪽엔 이미
등록돼 있을 수 있어 동일 요청이 중복 실행된다(CLAUDE.md — chat_runpod 어댑터
주의사항). 그래서 제출 실패는 재시도 대신 명확한 예외로 상위(`_invoke_with_retry`,
`app/modules/reply/graph.py`)에 알리고, 폴링(`_poll_until_done`)은 **처음 받은
동일 `job_id`만** 5초 간격·최대 5분(`_MAX_POLL * _POLL_INTERVAL`) 재확인한다.

## 두 경로 동작 비교 — 같은 파이프라인, 백엔드만 전환

이 환경에는 실제 RunPod 엔드포인트/키가 없어 "Ollama 대 RunPod 실호출"을 나란히
돌려볼 수는 없었다. 대신 검증한 것:

1. **배선이 특정 백엔드에 묶여 있지 않은지** — `factory.py`의 `get_llm_backend()`에
   `runpod` 분기 하나를 추가한 뒤, 기존 `LLM_BACKEND=ollama` 경로로 reply agent
   전체(plan→agent→judge→validate)가 여전히 정상 동작하는지 재확인했다
   (`pytest -m llm_live tests/test_reply.py` — 2/2 통과, 회귀 없음).
2. **`ChatRunPod`가 다른 백엔드와 동일한 인터페이스 계약을 지키는지** —
   `get_llm_backend()`가 `LLM_BACKEND=runpod`일 때 실제로 `ChatRunPod` 인스턴스를
   반환하고, 키가 없어도 생성자 시점엔 실패하지 않다가(다른 백엔드와 동일하게
   `RUNPOD_API_KEY`/`RUNPOD_ENDPOINT_ID` 검사는 실제 호출 시점 `_call_raw`에서만
   fail-fast) 확인했다(`tests/test_llm.py`).
3. **메시지·tool_calls 왕복 변환이 정확한지** — `_to_runpod_messages`/
   `_build_ai_message`/`RunPodBackend._payload`를 HTTP 호출 없이 순수 함수
   단위 테스트로 검증했다(`tests/test_llm.py`, 7개 케이스).

**실제 RunPod 엔드포인트 대상 e2e(진짜 job 제출→폴링→완료)는 검증하지 못했다** —
이건 실제 GPU 서버리스 엔드포인트를 배포한 뒤 사람이 확인해야 하는 항목이다
(Phase 10 배포 단계에서 재확인 예정).

## 파이프라인 규칙 재확인

파이프라인 코드(`app/modules/reply/`, `app/modules/triage/`)는 여전히 `ChatAnthropic`/
`ChatOpenAI`/`ChatRunPod`을 직접 import하지 않는다 — 전부 `app/common/llm/factory.py`
경유. 이 규칙이 지켜졌기 때문에 새 백엔드를 추가하는 데 `factory.py` 한 줄
분기만 필요했다.
