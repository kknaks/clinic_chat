"""허브 MCP 클라이언트(골드 · 원장) — 공식 MCP Python SDK `ClientSession` + Streamable HTTP 클라이언트 transport.

import 경로 확인(jev-research §4 · WORK-004 Phase 1) — 고정한 `mcp==2.3.0` 패키지 소스로 확인했다:
- `from mcp.client.streamable_http import streamable_http_client`
  (async context manager · `streamable_http_client(url, *, http_client=None, terminate_on_close=True)`
  → `(read_stream, write_stream)` 두 짝. 1.x 의 `streamablehttp_client` 3짝 반환과 다름)
- `from mcp.client.session import ClientSession`(= `mcp.ClientSession`) — `async with ClientSession(r, w) as s`
  → `await s.initialize()` · `await s.list_tools()` · `await s.call_tool(name, arguments)`
- mcp 2.x 모델 칸은 snake_case(`tool.input_schema` · `result.is_error` · `result.structured_content`)
- HTTP 클라이언트는 `httpx2`(mcp 2.x 의존) — 시험은 `httpx2.ASGITransport` 로 모의 허브에 포트 없이 붙는다
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


@dataclass
class ToolResult:
    text: str
    is_error: bool
    ms: float
    data: Any = None  # JSON 이면 파싱한 값


@dataclass
class HubMCP:
    """MCP 서버 하나(골드 또는 원장)에 붙는 세션 열기. `http_client_factory` 는 시험에서 ASGI transport 를 넣는 자리."""

    url: str
    http_client_factory: Any = None
    tools: list[dict] = field(default_factory=list)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[HubSession]:
        hc = self.http_client_factory() if self.http_client_factory else None
        try:
            async with streamable_http_client(self.url, http_client=hc) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                yield HubSession(s)
        finally:
            if hc is not None:
                await hc.aclose()


class HubSession:
    def __init__(self, s: ClientSession) -> None:
        self._s = s

    async def list_tools(self) -> list[dict]:
        """Anthropic 도구 꼴로 변환(`async_mcp_tool` 과 같은 꼴 — name · description · input_schema)."""
        res = await self._s.list_tools()
        return [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in res.tools
        ]

    async def list_tools_meta(self) -> dict[str, dict]:
        """도구 이름 → `_meta`(SPEC-005 §5-0-2 M-1 · 기동 확인 「도구 meta」)."""
        res = await self._s.list_tools()
        return {t.name: dict(t.meta or {}) for t in res.tools}

    async def call(self, name: str, arguments: dict) -> ToolResult:
        t = time.perf_counter()
        try:
            res = await self._s.call_tool(name, arguments)
        except Exception as e:  # noqa: BLE001 — 전송 오류도 is_error 결과로(루프는 계속)
            return ToolResult(text=f"MCP 오류: {e}", is_error=True, ms=(time.perf_counter() - t) * 1000)
        ms = (time.perf_counter() - t) * 1000
        parts = [c.text for c in res.content if getattr(c, "type", None) == "text"]
        text = "\n".join(parts)
        data = res.structured_content
        if data is None and text:
            try:
                data = json.loads(text)
            except ValueError:
                data = None
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        return ToolResult(text=text, is_error=bool(res.is_error), ms=ms, data=data)


def asgi_client_factory(app: Any, base_url: str = "http://fake-hub") -> Any:
    """시험 · 모의 판 — 포트를 열지 않고 ASGI 앱에 직접 붙는 httpx2 클라이언트."""

    def make() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=base_url, timeout=30)

    return make
