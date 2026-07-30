#!/usr/bin/env python3
"""노션 MCP 서버 실측 프로브 (Phase 12b) — 읽기 전용, 앱 코드는 건드리지 않는다.

측정 대상(MCP_INTEGRATION.md에 "실측(날짜)" 형식으로 기록할 7개, PROMPTS.md
Phase 12b 참고): 전송/인증, 프로토콜 세대, 엔드포인트 경로, 서버 필수 env·
실행 인자, tools/list 전체 + 조회 도구 스키마, 응답 모양, 조회 1회 latency.

URL/토큰은 env에서만 읽는다(인자로 받지 않는다) — 실행 전 아래를 준비해둘 것:
  NOTION_MCP_URL   예: http://127.0.0.1:3300/mcp
  NOTION_MCP_TOKEN 서버 앞단 인증 토큰(AUTH_TOKEN과 동일해야 함)
  NOTICE_DB_ID     노션 공지 DB의 database_id(URL에서 뽑은 32자리)

[엄수] 쓰는 호출(생성/수정/삭제) 없음. 토큰·공지 본문 전문은 출력하지 않는다
— 필드명·구조·길이만 기록한다(하드룰 4).
"""
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import anyio  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402


def _find_tool(tools, *name_hints):
    for hint in name_hints:
        for t in tools:
            if hint.lower() in t.name.lower():
                return t
    return None


async def main() -> None:
    url = os.environ["NOTION_MCP_URL"]
    token = os.environ["NOTION_MCP_TOKEN"]
    db_id = os.environ["NOTICE_DB_ID"]
    headers = {"Authorization": f"Bearer {token}"}

    async with streamablehttp_client(url, headers=headers, timeout=10.0) as (
        read_stream, write_stream, get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            print("=== 1. 전송/인증 + 2. 프로토콜 세대 + 3. 엔드포인트 ===")
            init_result = await session.initialize()
            print(f"연결 성공 (streamable HTTP + Bearer 정적 토큰) — URL: {url}")
            print(f"protocolVersion: {init_result.protocolVersion}")
            print(f"serverInfo: {init_result.serverInfo}")
            sid = get_session_id()
            print(f"mcp-session-id 발급 여부: {sid is not None} ({'있음' if sid else '없음'})")

            print("\n=== 5. tools/list 전체 목록 + inputSchema ===")
            tools_result = await session.list_tools()
            tools = tools_result.tools
            print(f"도구 개수: {len(tools)}")
            for t in tools:
                props = list((t.inputSchema or {}).get("properties", {}).keys())
                required = (t.inputSchema or {}).get("required", [])
                print(f"  - {t.name}: properties={props} required={required}")

            retrieve_db_tool = _find_tool(tools, "retrieve-a-database")
            query_ds_tool = _find_tool(tools, "query-data-source")
            print(f"\nDB 메타 조회 도구: {retrieve_db_tool.name if retrieve_db_tool else 'NOT FOUND'}")
            print(f"데이터소스 조회 도구: {query_ds_tool.name if query_ds_tool else 'NOT FOUND'}")

            print("\n=== database_id -> data_source_id 해석 (2025-09-03+ API 모델) ===")
            data_source_id = db_id  # 폴백: 해석 실패 시 database_id를 그대로 시도
            if retrieve_db_tool:
                db_result = await session.call_tool(retrieve_db_tool.name, {"database_id": db_id})
                db_text = getattr(db_result.content[0], "text", "") if db_result.content else ""
                try:
                    db_json = json.loads(db_text)
                    print(f"retrieve-a-database 최상위 키: {list(db_json.keys())}")
                    if db_json.get("object") == "error":
                        print(f"에러 응답: code={db_json.get('code')} message={db_json.get('message')}")
                    data_sources = db_json.get("data_sources", [])
                    print(f"database.data_sources: {data_sources}")
                    if data_sources:
                        data_source_id = data_sources[0]["id"]
                        print(f"-> data_source_id로 사용: {data_source_id}")
                except json.JSONDecodeError:
                    print(f"JSON 파싱 실패 — 원문 앞 200자: {db_text[:200]!r}")

            print("\n=== 6. 응답 모양 (데이터소스 조회 1회) ===")
            latencies = []
            sample_text = None
            for i in range(3):
                start = time.monotonic()
                try:
                    result = await session.call_tool(
                        query_ds_tool.name, {"data_source_id": data_source_id}
                    )
                except Exception as e:
                    print(f"  호출 {i+1} 실패: {type(e).__name__}: {e}")
                    break
                elapsed = time.monotonic() - start
                latencies.append(elapsed)
                print(f"  호출 {i+1}: {elapsed*1000:.0f}ms, isError={result.isError}")
                if sample_text is None and result.content:
                    sample_text = getattr(result.content[0], "text", None)

            if sample_text:
                print(f"\n텍스트 길이: {len(sample_text)} chars")
                try:
                    parsed = json.loads(sample_text)
                    print(f"최상위 키: {list(parsed.keys())}")
                    if parsed.get("object") == "error":
                        print(f"에러 응답: code={parsed.get('code')} message={parsed.get('message')}")
                    results = parsed.get("results", [])
                    print(f"결과 건수: {len(results)}, has_more: {parsed.get('has_more')}, "
                          f"next_cursor: {parsed.get('next_cursor')}")
                    if results:
                        first = results[0]
                        print(f"레코드 최상위 키: {list(first.keys())}")
                        props = first.get("properties", {})
                        print(f"프로퍼티 이름: {list(props.keys())}")
                        for pname, pval in props.items():
                            print(f"  {pname}: type={pval.get('type')}, "
                                  f"필드 경로 예시={list(pval.keys())}")
                except json.JSONDecodeError:
                    print(f"구조화 JSON 아님(마크다운/텍스트 추정) — 앞 200자: {sample_text[:200]!r}")

            print("\n=== 7. latency (3회 평균) ===")
            if latencies:
                avg = sum(latencies) / len(latencies)
                print(f"평균: {avg*1000:.0f}ms (개별: {[f'{x*1000:.0f}ms' for x in latencies]})")
            else:
                print("측정 실패 — 위 오류 참고")


if __name__ == "__main__":
    anyio.run(main)
