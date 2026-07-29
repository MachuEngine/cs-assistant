# MCP_INTEGRATION.md — MCP 연동 설계 (Slack 에스컬레이션 알림)

> **상태: 설계 확정. 구현 없음.**
> 이 문서는 "무엇을 어떻게 만들 것인가"를 확정하기 위한 것이고, 코드는 아직 없다.
>
> **대상 서버: `zencoderai/slack-mcp` (구 스펙 / MCP SDK v1 라인) — 2026-07-29 확정.**
> 선정 근거와 후보 비교는 2절.

관련 문서: 판단 기준은 `VENDOR_INTEGRATION.md`(정식 지원 vs 커스텀 어댑터), 보안 하드룰은
`CLAUDE.md`, API 계약은 `DESIGN.md` 7절.

---

## 0. 왜 하는가

지금 `escalated`는 **막다른 길**이다. E1~E8 판정은 전부 결정론적 코드로 정확히 동작하지만,
outcome이 `escalated`로 나온 뒤 **실제로 사람을 부르는 경로가 없다**. HITL(상담원 증강)이 이
프로젝트의 정체성인데 "사람에게 넘긴다"의 후반부가 비어 있다.

**범위**: Slack 에스컬레이션 알림 하나만. Notion HITL 검토와 GitHub eval 코멘트는 범위 밖
(사유는 6절).

---

## 1. 전제 — 확정된 제약

| 제약 | 출처 | 영향 |
|---|---|---|
| 티켓 본문·초안을 외부로 내보내지 않는다 | 하드룰 3 유지(사용자 결정, 2026-07-29) | 알림 페이로드는 식별자·메타데이터만 |
| `customer_id`도 제외 | 사용자 결정 — 사람이 알아볼 수 없는 값이라 실익 0, 외부 반출만 증가 | 페이로드에서 제외 |
| 도구는 순수 계산·검색·저장만 | `CLAUDE.md` 모듈별 주의사항 | MCP 호출을 `reply/tools.py`에 넣지 않는다 |
| 도구는 동기 호출된다 | `graph.py:agent_node`의 `fn.invoke(...)` | 도구 안에 async I/O를 넣으면 전 도구 async화 필요 |
| 노드 순서 변경은 설계 승인 필요 | `CLAUDE.md` reply 모듈 | 그래프에 노드를 추가하지 않는다 |

---

## 2. 대상 서버 선정 (2026-07-29)

**공식 Slack MCP 서버는 존재하지 않는다.** 커뮤니티 구현 3종을 조사해 비교했다.

| | **korotovsky** | **zencoderai** ✅ | **ubie-oss** |
|---|---|---|---|
| 언어/SDK | Go | TypeScript, MCP SDK **v1.13.2** | Node/TS (버전 미명시) |
| 전송 | Stdio·SSE·HTTP | Stdio·**Streamable HTTP** | Stdio·**Streamable HTTP** |
| Slack 인증 | `xoxb`/`xoxp`/**`xoxc`+`xoxd`(세션)** | **`xoxb` 전용** | `xoxb`+`xoxp` **둘 다 필수** |
| 서버 앞단 인증 | ✅ `SLACK_MCP_API_KEY`(Bearer) | ✅ `AUTH_TOKEN`(**미설정 시 자동 생성·강제**, 실측) | 명시 없음 |
| Docker | Dockerfile만 | ✅ **Docker Hub 이미지** | Dockerfile만 |
| 발송 도구명 | `conversations_add_message` | `slack_post_message` | `slack_post_message` |
| 인자 | (도구별 상이) | `channel_id`, `text` | 미명시 |

**`zencoderai/slack-mcp` 선정 이유**
- **`xoxb` 봇 토큰만 사용하는 유일한 후보.** korotovsky는 세션 토큰(개인 계정 자격증명)
  경로가 열려 있어 프로덕션 부적합이고, ubie-oss는 `xoxp`(유저 토큰)까지 필수라 권한 범위가
  불필요하게 넓다
- **Docker Hub 공식 이미지**(`zencoderai/slack-mcp:latest`) — `docker-compose.yml`에 서비스
  한 줄로 붙는다. 나머지 둘은 직접 빌드해야 한다
- Streamable HTTP 지원 — 우리 FastAPI에서 호출하려면 필수(stdio는 로컬 데스크톱 클라이언트용)
- **SDK 버전이 명시돼 있다**(v1.13.2 = 구 스펙 v1 라인) → 우리 클라이언트 핀과 세대가 맞는다

**앞단 인증**: 문서엔 없었지만 실측 결과 `AUTH_TOKEN`이 있고, **미설정 시 컨테이너가 UUID를
자동 생성해 강제**한다(기동 로그에 출력). 값을 명시적으로 고정해야 재기동 때마다 바뀌지 않는다.
그래도 도커 내부 네트워크에만 노출하고 호스트 포트는 열지 않는다(2중 방어).

### 프로토콜 세대: 구 스펙(stateful)으로 확정 — 실측

**2026-07-29 실제 컨테이너로 확인했다**(`zencoderai/slack-mcp:latest`, digest `sha256:d47ca0d…`):

```
initialize 응답 → protocolVersion: "2025-03-26"
응답 헤더       → mcp-session-id: ef6caa3e-…      ← 세션 발급 = stateful
기동 로그       → "Streamable HTTP transport on port 3000"
```

2026-07-28 stateless가 아니라 **2025-03-26 세대**다. 따라서 우리 클라이언트도
**v1 라인(`mcp>=1.28,<2`)** 으로 맞춘다. 신 스펙이 최소 12개월 유예를 두므로 당장 강제
마이그레이션되지 않는다.

> **stateless 전환은 "나중에 서버가 따라오면"의 문제다.** 그때 `client.py` 한 파일만
> 교체하면 되도록 SDK를 직접 노출하지 않고 감싼다(3.2절).

### 실측 검증 현황 (2026-07-29)

| 항목 | 결과 |
|---|---|
| 컨테이너 기동 · HTTP 전송 | ✅ 확인 |
| `initialize` 핸드셰이크 · 세션 발급 | ✅ 확인 (`2025-03-26`, `mcp-session-id`) |
| `tools/list` 도구 8개 · 스키마 | ✅ 확인 |
| 도구 자동 발견 알고리즘 | ✅ 실제 스키마로 검증 — 단, 필수 인자 필터 필요성 발견(4.3절) |
| `tools/call` 실제 Slack 발송 | ✅ **성공** — 봇을 채널에 초대한 뒤 재시도, `ok:true` + 메시지 `ts` 반환 |

**전 구간이 실측으로 검증됐다.** 남은 미검증 항목 없음 — 설계가 실제 동작하는 경로 위에 있다.
(첫 시도는 `not_in_channel`로 실패했고 원인·해결은 8.1절에 기록. 최종적으로 채널 초대
방식으로 해소.)

→ **폴백**: MCP 경로가 막히면 동일한 `EscalationNotifier` 인터페이스 뒤에서 Slack Web API
(`chat.postMessage`)를 직접 호출한다. 상위 코드는 무변경 — 3절 추상화의 실질적 이유 중 하나다.

---

## 3. 아키텍처 배치 (B)

### 3.1 레이어 위치

```
app/common/mcp/              ← 신설. app/common/llm/ 과 대칭 구조
├── base.py                  EscalationNotifier ABC   (llm/base.py 와 같은 역할)
├── client.py                MCP 프로토콜 계층 — 공식 `mcp` SDK 얇게 감싼다
├── factory.py               get_notifier()           (llm/factory.py 와 같은 역할)
└── backends/
    ├── noop.py              기본값
    └── slack.py             도메인 계층               (chat_runpod.py 와 같은 역할)
```

**`client.py`(프로토콜)와 `backends/slack.py`(도메인)를 나누는 이유**는 기존
`runpod.py`(HTTP job queue) / `chat_runpod.py`(LangChain 어댑터) 분리와 정확히 같다 —
"프로토콜을 말하는 코드"와 "우리 도메인 의미를 아는 코드"를 섞지 않는다. `client.py`는
Slack을 모르고, `slack.py`는 MCP 세부를 모른다.

`client.py`가 SDK를 직접 노출하지 않고 한 겹 감싸는 이유: `mcp` 2.x는 릴리스 직후라 API가
움직일 수 있고(4.1절 핀 참고), SDK 교체·버전 업 시 영향 범위를 이 파일 하나로 가둔다.

### 3.2 `common/llm`과의 관계 — 대칭이되 기본값이 반대

| | `common/llm` | `common/mcp` |
|---|---|---|
| 실패 정책 | **fail-fast** — 키 없으면 즉시 예외 | **fail-soft** — 실패해도 파이프라인 계속 |
| 미설정 시 | 에러 | `noop`(조용히 비활성) |
| 이유 | 생성 실패를 숨기면 검증 안 된 출력이 나간다 | 알림 실패로 멀쩡한 에스컬레이션 판정을 잃으면 안 된다 |

이 **의도적 비대칭**이 설계의 핵심이다. 같은 팩토리 패턴을 쓰되 정책이 반대인 이유를 코드
주석에 명시한다.

### 3.3 호출 지점 — 도구가 아니라 서비스 계층

```
                       ┌─────────────────────────────────────┐
  POST /reply          │  app/main.py (서비스 계층)          │
  POST /reply/stream ─►│                                     │
                       │  triage_ticket()                    │
                       │      │                              │
                       │      ├─ E1~E4 확정 ──────┐          │
                       │      │                   │          │
                       │      ▼                   │          │
                       │  run_reply()/stream_reply()│         │
                       │   (그래프 전체 무변경)     │          │
                       │      │                   │          │
                       │      ├─ E5~E8 확정 ──────┤          │
                       │      │                   ▼          │
                       │      │        _notify_escalation()  │
                       │      │                   │          │
                       │      ▼                   ▼          │
                       │   응답 반환      app/common/mcp/    │
                       └─────────────────────────────────────┘
```

**도구(`tools.py`) 안에 넣지 않는 이유 3가지** — 전부 기존 코드 제약에서 나온다:

1. `CLAUDE.md` 명시: "모든 도구는 LLM 호출 없이 순수 계산·검색·저장만"
2. 도구는 `agent_node`에서 **동기** 호출(`fn.invoke(...)`)된다 → async MCP 호출을 넣으려면
   전 도구 async화 + 에이전트 루프 변경
3. 도구는 **재시도 루프 안**이다 → budget 소진까지 최대 3회 도는 동안 **중복 발송**

결과적으로 `graph.py` 노드 순서·`tools.py` 도구 순수성·가드레일 로직이 **전부 무변경**이다.

### 3.4 에스컬레이션 확정 지점 4곳

| # | 위치 | 사유 |
|---|---|---|
| 1 | `/reply` triage 직후 | E1~E4 |
| 2 | `/reply` `run_reply()` 반환 후 | E5~E8 |
| 3 | `/reply/stream` triage 직후 | E1~E4 |
| 4 | `/reply/stream` 최종 `done` 이벤트 직전 | E5~E8 |

호출 지점은 4곳이지만 **헬퍼는 하나**(`_notify_escalation`)로 두어 정책을 한 곳에 모은다.

### 3.5 실패 전파 경계 (핵심)

```
SlackNotifier.notify_escalation()   ← 자기 예외를 내부에서 삼킴 (base.py 계약)
        ▲
_notify_escalation()                ← 팩토리 오설정까지 여기서 한 번 더 차단
        ▲
/reply의 try/except                 ← 여기까지 예외가 오면 outcome=failed 로 뒤바뀜 (막아야 함)
```

`/reply`는 전체를 `try/except`로 감싸 예외 시 `{"outcome": "failed"}`를 반환한다. Slack 장애가
**멀쩡한 에스컬레이션 판정을 `failed`로 뒤집는 것**이 이 설계에서 가장 피해야 할 실패 모드다.
그래서 방어를 2중으로 둔다(백엔드 내부 + 헬퍼).

타임아웃은 짧게(기본 5초). 에스컬레이션 알림이 API 응답을 붙잡고 있으면 안 된다.

---

## 4. 프로토콜 흐름 (A)

### 4.1 SDK와 버전 핀

**구현은 공식 Python SDK(`mcp`)를 쓴다.** 헤더 구성·버전 협상·에러 스키마처럼 직접 짜면
틀리기 쉬운 부분을 SDK가 처리한다. `runpod.py`를 손으로 짠 것과는 상황이 다르다 — RunPod은
공식 LangChain 통합이 **없어서** 어댑터가 필요했지만(`VENDOR_INTEGRATION.md` 판단 기준표),
MCP는 공식 SDK가 있고 그게 정식 지원 경로다.

**버전 핀: `mcp>=1.28,<2`**

| 라인 | 스펙 | 우리 선택 |
|---|---|---|
| `mcp` 1.28.x | 구 스펙(stateful, initialize 핸드셰이크) | ✅ **대상 서버(zencoderai SDK v1.13.2)와 세대 일치** |
| `mcp` 2.0.0 | 2026-07-28 stateless | ✗ 서버가 아직 구 스펙 |

- **상한(`<2`)은 필수다.** `pip install mcp`가 이제 2.x를 가져오므로 상한이 없으면 신 스펙
  클라이언트가 설치돼 구 스펙 서버와 어긋난다
- 이 프로젝트는 상한 없는 의존성으로 이미 한 번 깨졌다 — FlagEmbedding이
  `transformers<6.0.0`만 걸어둬서 5.x에서 파손 → `transformers==4.57.6` 핀(Phase 3).
  같은 실수를 반복하지 않는다

### 4.1.1 stateless 전환 경로 (지금은 안 함)

2026-07-28 스펙은 `initialize` 핸드셰이크와 `Mcp-Session-Id`를 제거해, 클라이언트가
"Bearer 토큰 붙인 단발 JSON-RPC POST" 수준으로 단순해진다. 우리 사용 패턴(단발 알림)은
stateless에 이상적으로 맞지만, **대상 서버가 아직 구 스펙이라 지금 전환하지 않는다.**

전환 시점에 바꿀 것: `requirements.txt` 핀 → `mcp>=2.0,<3`, `client.py`의 세션 수립 부분
제거. **`base.py`/`factory.py`/`backends/slack.py`/`app/main.py`는 무변경**이다 — 그러라고
`client.py`로 격리했다.

### 4.2 시퀀스 (구 스펙 — 세션 있음)

```
  서비스 계층          MCPClient              MCP 서버
      │                   │                      │
      │  notify(...)      │                      │
      ├──────────────────►│                      │
      │                   │  initialize          │   ← 구 스펙: 세션 수립 필요
      │                   ├─────────────────────►│      (stateless에선 사라지는 단계)
      │                   │  capabilities        │
      │                   │◄─────────────────────┤
      │                   │                      │
      │                   │  tools/list          │   ← 캐시 미스일 때만
      │                   ├─────────────────────►│
      │                   │  {tools:[{name,      │
      │                   │    inputSchema},...]}│
      │                   │◄─────────────────────┤
      │                   │                      │
      │            [도구 선택 + 스키마 기반 인자 구성]
      │                   │                      │
      │                   │  tools/call          │
      │                   ├─────────────────────►│
      │                   │  {content:[...],     │
      │                   │   isError:false}     │
      │                   │◄─────────────────────┤
      │                   │  (세션 종료)          │
      │  True/False       │                      │
      │◄──────────────────┤                      │
```

**세션은 알림 1건 범위에서만 열고 닫는다.** 장기 연결을 유지하지 않는다 — 에스컬레이션은
드문 이벤트라 연결을 붙잡고 있을 이유가 없고, 그래야 4.1.1절의 stateless 전환 때 이
`initialize` 단계만 빠지고 나머지는 그대로 남는다.

### 4.3 도구 발견 — 이름을 코드에 박지 않는다

MCP가 존재하는 이유가 "클라이언트가 서버 도구를 사전 지식 없이 발견"하는 것이다. 도구 이름을
하드코딩하면 MCP를 고정 REST 엔드포인트처럼 쓰는 것이고, 2절에서 확인한 서버 파편화 문제에
그대로 종속된다.

**선택 알고리즘**(우선순위 순):

1. `SLACK_MCP_TOOL_NAME`이 설정돼 있으면 → **발견된 목록 안에서** 그 이름을 찾는다. 없으면
   에러(조용히 다른 도구로 대체하지 않는다). 이건 발견을 건너뛰는 게 아니라 자동 선택이
   틀렸을 때의 탈출구다.
2. 아니면 **스키마 모양으로 후보를 거른다** — `inputSchema.properties`에 채널류 키
   (`channel_id`/`channel`/`conversation_id`…)와 텍스트류 키(`text`/`message`/`content`…)가
   둘 다 있는 도구. 이름보다 스키마가 신뢰도 높다.
3. **`required` 인자를 전부 채울 수 있는 도구만 남긴다**(아래 실측 근거).
4. 그래도 여럿이면 이름 힌트로 순위(`post`+`message` > `send`+`message` > …).
5. 후보가 없으면 → 명확한 에러 + 사용 가능한 도구 목록을 로그에 남긴다.

> **3번이 왜 필요한지 — 실측(2026-07-29).** 대상 서버의 도구 8개를 `tools/list`로 뽑아
> 스키마 필터(2번)를 적용하면 후보가 **2개** 나온다:
>
> | 도구 | 인자 | 필수 | 채울 수 있나 |
> |---|---|---|---|
> | `slack_post_message` | `channel_id`, `text` | 둘 다 | ✅ |
> | `slack_reply_to_thread` | `channel_id`, `thread_ts`, `text` | 셋 다 | ❌ `thread_ts` 없음 |
>
> 이번엔 이름 힌트(4번)가 `slack_post_message`를 먼저 골라 우연히 맞지만, **이름이 다른
> 서버에서는 스레드 답장 도구가 선택돼 `thread_ts` 누락으로 실패**할 수 있다. 필수 인자
> 충족 여부를 순위보다 먼저 거르는 게 안전하다.

**실측된 도구 목록**(`zencoderai/slack-mcp:latest`, 참고용 — 코드는 런타임에 읽는다):
`slack_post_message` · `slack_reply_to_thread` · `slack_list_channels` ·
`slack_add_reaction` · `slack_get_channel_history` · `slack_get_thread_replies` ·
`slack_get_users` · `slack_get_user_profile`

> **스키마 필터를 1차로 두는 게 왜 중요한지 — 실제 사례.** 조사한 3개 서버 중
> `korotovsky`의 발송 도구명은 `conversations_add_message`다. 이름 힌트(`post`+`message`,
> `send`+`message`)에 **하나도 안 걸린다.** 이름으로 먼저 거르는 설계였다면 이 서버를 아예
> 못 붙였을 것이다. 스키마 모양(채널류 키 + 텍스트류 키)으로 거르면 정상적으로 잡힌다.
> 선정한 zencoderai는 `slack_post_message`라 양쪽 다 걸리지만, 서버를 갈아끼울 때를 위해
> 이 순서를 유지한다.

**인자 구성도 스키마 기반**이다. 서버가 준 `inputSchema.properties`에서 채널/텍스트 키를 찾아
채우고, `required`인데 채울 수 없는 인자가 있으면 **호출하지 않고 실패시킨다** — 형식이 깨진
호출을 보내는 것보다 낫다. (zencoderai 기준 `channel_id`/`text`지만, 이건 코드가 런타임에
읽는 값이지 하드코딩 대상이 아니다.)

### 4.4 캐싱

`tools/list` 결과는 프로세스별 in-memory로 캐시한다. 에스컬레이션마다 도구 목록을 다시 물을
이유가 없다. 캐시가 비어도 동작은 같으므로(다시 조회할 뿐) **순수 성능 최적화**이며, 테스트·
설정 변경용으로 캐시 무효화 함수를 둔다.

### 4.5 재시도 — 하지 않는다

자동 재시도를 넣지 않는다. 응답을 못 받은 상태에서 재시도하면 **첫 요청이 이미 발송됐을 수
있어 중복 알림**이 된다. 이 프로젝트는 같은 함정을 RunPod 어댑터에서 이미 겪었다
(`runpod.py`: "제출 응답을 못 받아도 재제출하지 않는다 — 중복 실행이 된다"). 알림 1건이
유실되는 것이 중복 발송으로 상담원 신뢰를 깎는 것보다 낫다.

> 향후 Notion처럼 **쓰기**가 생기면 멱등성이 필수가 된다(`ticket_id`를 유니크 키로 upsert).
> 알림은 부수효과가 가벼워 이번 범위에서는 불필요.

---

## 5. 페이로드 계약 (하드룰 3 준수)

| | 항목 |
|---|---|
| **포함** | `ticket_ref`(있으면) · 요청 ID(`REQ-…`) · intent · category · confidence · 사유 코드(E1~E8) + 설명 · 발생 시각(UTC) |
| **제외** | 티켓 본문 · 초안 전문 · `customer_id` · 이메일/전화/카드/주소/인명 |

**E1~E8 설명은 `routing.py`의 `ESCALATION_REASONS`를 재사용한다.** 여기서 한글 라벨을 새로
만들면 에스컬레이션 사유의 단일 출처가 둘로 갈라진다. 코드+영문 설명을 그대로 싣고 주변
문구만 한국어로 쓴다.

### `ticket_ref` — 신설 필요 (API 계약 변경)

현재 `ticket_id`는 `main.py`가 요청마다 만드는 `REQ-{uuid12}`이고, **이 시스템은 티켓을
아무것도 영구 저장하지 않는다**(그래서 하드룰 3이 자동으로 지켜진다). 즉 우리 UI로 딥링크를
걸면 **열 게 없다 — 404다.**

현실에서 이 서비스는 외부 CS 시스템(Zendesk 등)이 호출하고 **진짜 티켓은 호출자가 소유**한다.
따라서 `/reply`·`/reply/stream` 요청 바디에 `ticket_ref: str = ""`(외부 티켓 ID 또는 URL,
불투명 문자열)를 추가하고 알림에 그대로 되돌려준다. 우리는 여전히 아무것도 저장하지 않는다.
미제공 시(포트폴리오 데모)는 내부 `REQ-` ID만 표시하고 링크는 생략한다.

---

## 6. 범위에서 제외한 것과 그 이유

**Notion HITL 검토** — 하드룰 3을 유지하기로 한 결정과 충돌한다. HITL의 핵심은 "검토자가
**초안을 읽고** 승인/거부"인데 초안을 Notion에 못 보내면 `ticket_id + status`만 있는 껍데기
상태표가 된다. 검토자는 초안 읽으러 우리 UI, 승인하러 Notion으로 이중 왕복하게 되어 이미
있는 Next.js 검토 화면에 승인 버튼을 다는 것보다 나쁘다. 추가로, 승인 루프는 본질적으로
"나중에 다시 꺼내 보는 것"이라 **초안 영구 저장소**가 전제되는데 이는 MCP와 별개의 설계
사안이다.

**GitHub eval PR 코멘트** — MCP가 틀린 도구다. CI에는 이미 `GITHUB_TOKEN`이 있고
`gh pr comment` 한 줄이면 된다. MCP의 가치는 LLM 에이전트가 도구를 동적으로 발견·선택할 때
나오는데 CI 흐름은 완전히 결정론적이고 에이전트가 없다. 더 근본적으로 **자동 eval 자체가 이
프로젝트 규칙과 충돌**한다 — `DESIGN.md` 6.4절과 `guard_eval_cost.py`가 "전체 eval은 사람이
실행"을 강제하고 `ci.yml`도 "모델 호출 없음"이 원칙이다. 자동으로 돌려도 되는 건 비용 0인
`run_pii.py` 하나뿐이다.

---

## 7. 구현 시 변경 대상 (참고 — 아직 하지 않음)

- **신규**: `app/common/mcp/**`, `tests/test_mcp.py`
- **`requirements.txt`**: `mcp>=1.28,<2` 추가(4.1절 — 상한 필수)
- **`docker-compose.yml`**: `slack-mcp` 서비스 추가(8절)
- **`app/main.py`**: `ReplyRequest`에 `ticket_ref`, `_notify_escalation()` 헬퍼, 4개 지점 호출,
  SSE에 `stage: "notify"` 진행 이벤트 1개
- **`.env.example`**: `MCP_NOTIFIER=noop` · `SLACK_MCP_URL` · `SLACK_MCP_TOKEN` ·
  `SLACK_ESCALATION_CHANNEL` · `SLACK_MCP_TOOL_NAME`(선택) · `MCP_NOTIFY_TIMEOUT=5`
- **`DESIGN.md`**: 7절에 `ticket_ref`, 8절 환경변수 표, 10절에 MCP 판단 근거
- **`CLAUDE.md`**: 모듈별 주의사항에 MCP 항목(도구 안에 넣지 말 것 / fail-soft / 최소 페이로드)
- **`frontend/`**: 변경 없음 — `stage: "notify"`는 기존 진행 표시 로직이 그대로 처리
- **기존 테스트 1건 갱신 필요**: `test_reply_stream_escalated_has_no_draft_anywhere`가 SSE
  이벤트 개수를 2로 단정하는데, `notify` 이벤트가 추가되면 3이 된다. **핵심 단정("draft 관련
  키가 어디에도 없다")은 그대로 두고 개수만 갱신**한다 — 테스트를 약화시키는 게 아니라
  의도한 동작 변경을 반영하는 것.

### 검증 계획

1. `pytest -q -m "not rag and not llm_live"` — 기존 107개 회귀 없음
2. **가드레일 테스트(핵심)**: 알림 페이로드에 티켓 본문·초안·`customer_id`·PII 패턴이 하나도
   없는지 단정. `save_draft` 게이트 테스트와 같은 성격 — "실수로 새는 것"을 코드가 막는지 확인
3. `MCP_NOTIFIER` 미설정 시 아무 호출도 안 나가고 기존 응답 shape가 동일한지
4. notifier가 예외를 던져도 `/reply`가 `escalated`를 정상 반환하는지(fail-soft, 강제 실패 주입)
5. 도구 발견 로직: 스키마 기반 선택, 명시 지정, 후보 없음(에러) 각각
6. **실제 Slack 발송은 워크스페이스·토큰이 있어야 하므로 사람이 수동 확인** — 안 되면
   `EVAL.md`/`MEMORY.md` 관례대로 보류 사유를 기록한다(RunPod e2e와 동일한 처리)

---

## 8. 배포 — 사람이 준비해야 하는 것

코드 구현과 별개로 아래는 사람이 직접 해야 한다.

### 8.1 Slack 앱 (봇 토큰 발급)

api.slack.com/apps → **Create New App → From an app manifest** 에 아래를 붙여넣는다.
"From scratch"로 만들고 스코프를 수동 추가해도 결과는 같다.

```yaml
display_information:
  name: CS Assistant Escalation
  description: CS 티켓 에스컬레이션 알림 봇
features:
  bot_user:
    display_name: CS Assistant
    always_online: false
oauth_config:
  scopes:
    bot:
      - chat:write
      - chat:write.public
settings:
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

- `chat:write` — 필수(메시지 발송)
- `chat:write.public` — **공개 채널에 봇 초대 없이** 쓸 수 있게 한다
- Install to Workspace → **`xoxb-`로 시작하는 Bot User OAuth Token** 확보
- 알림 채널의 **채널 ID**(`C0XXXXXXX`) 확보 — 채널명보다 ID가 안전하다
- **워크스페이스 ID**(`T…`)도 필요하다 — MCP 서버가 `SLACK_TEAM_ID`로 요구한다.
  `curl -H "Authorization: Bearer xoxb-…" https://slack.com/api/auth.test` 의 `team_id`

> ⚠️ **`not_in_channel` — 실제로 밟은 함정(2026-07-29).** 테스트 발송이 이 에러로 실패했고,
> 원인은 토큰에 부여된 스코프가 `channels:history, chat:write`뿐이라 `chat:write.public`이
> 빠진 것이었다. 확인 방법은 Slack API 응답 헤더의 `x-oauth-scopes`다:
> ```
> curl -s -D - -o /dev/null -H "Authorization: Bearer xoxb-…" \
>   https://slack.com/api/auth.test | grep -i x-oauth-scopes
> ```
> **해결 2가지** — ① 채널에서 `/invite @봇이름`(재설치 불필요, 비공개 채널도 가능)
> ② `chat:write.public` 스코프 추가 후 **Reinstall to Workspace**(공개 채널 전체에 적용).
> manifest로 앱을 만들어도 **나중에 스코프를 바꾸면 재설치해야 반영된다.**
>
> 이 프로젝트는 ①(채널 초대)로 해소했고, 이후 발송이 정상 동작함을 확인했다.
> 알림 채널 하나만 쓰는 구성에서는 ①이 더 간단하다.

### 8.2 MCP 서버 컨테이너

`xoxb-` 토큰은 **우리 `.env`가 아니라 MCP 서버 쪽**에 들어간다. 우리 앱은 Slack을 직접
부르지 않는다.

아래는 **실제 컨테이너를 띄워 검증한 값**이다(2026-07-29).

```yaml
# docker-compose.yml 에 추가
  slack-mcp:
    image: zencoderai/slack-mcp:latest
    command: ["--transport", "http"]      # 없으면 stdio로 떠서 HTTP 호출 불가
    environment:
      SLACK_BOT_TOKEN: ${SLACK_BOT_TOKEN}   # xoxb-…
      SLACK_TEAM_ID: ${SLACK_TEAM_ID}       # T… (필수)
      AUTH_TOKEN: ${SLACK_MCP_TOKEN}        # 고정하지 않으면 기동마다 UUID 자동 생성
    # [엄수] ports를 열지 않는다 — 도커 내부 네트워크에서만 접근 가능해야 한다.
    expose:
      - "3000"
```

**실측 확인 사항**
- 엔드포인트는 루트가 아니라 **`/mcp`** 다 (`http://0.0.0.0:3000/mcp`)
- `--transport http` 를 안 주면 stdio로 기동한다
- `AUTH_TOKEN` 미설정 시 기동 로그에 자동 생성된 UUID를 출력하고 그걸 강제한다 →
  재기동마다 값이 바뀌므로 **반드시 명시적으로 고정**한다

### 8.3 우리 앱 `.env`

```bash
MCP_NOTIFIER=slack
SLACK_MCP_URL=http://slack-mcp:3000/mcp  # 엔드포인트 경로 /mcp 포함(실측)
SLACK_MCP_TOKEN=                          # 서버 AUTH_TOKEN과 같은 값을 넣는다
SLACK_ESCALATION_CHANNEL=C0XXXXXXX
SLACK_TEAM_ID=T0XXXXXXXXX                 # MCP 서버가 요구
SLACK_BOT_TOKEN=xoxb-…                    # MCP 서버로 전달됨(우리 앱은 안 씀)
SLACK_MCP_TOOL_NAME=                      # 자동 발견 실패 시에만 지정
MCP_NOTIFY_TIMEOUT=5
```
