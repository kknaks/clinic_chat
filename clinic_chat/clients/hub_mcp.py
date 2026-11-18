"""허브 gold MCP 클라이언트 — E4 확인 · 시험에서만 쓴다(C 열의 도구 호출은 CLI 가 `--mcp-config` http 로 직접 한다).

import 경로(옛 틀 메모 · `mcp==2.3.0`):
- `from mcp.client.streamable_http import streamable_http_client` → `(read_stream, write_stream)` 두 짝
- `from mcp.client.session import ClientSession` → `initialize()` · `list_tools()` · `call_tool(name, arguments)`
- mcp 2.x 모델 칸은 snake_case(`tool.input_schema` · `result.is_error` · `result.structured_content`)
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


@dataclass
class ToolResult:
    text: str
    is_error: bool
    ms: float
    data: Any = None


@dataclass
class HubMCP:
    url: str
    http_client_factory: Any = None

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
        res = await self._s.list_tools()
        return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                for t in res.tools]

    async def call(self, name: str, arguments: dict) -> ToolResult:
        t = time.perf_counter()
        res = await self._s.call_tool(name, arguments)
        ms = (time.perf_counter() - t) * 1000
        text = "\n".join(c.text for c in res.content if getattr(c, "type", None) == "text")
        data = res.structured_content
        if data is None and text:
            try:
                data = json.loads(text)
            except ValueError:
                data = None
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        return ToolResult(text=text, is_error=bool(res.is_error), ms=ms, data=data)
