"""모의 허브 — 골드 MCP(9) · 원장 MCP(3) · 골드 REST. Streamable HTTP · stateless · JSON(SPEC-005 §1-3 · §5-12).

시험은 포트를 열지 않고 `httpx2.ASGITransport` 로 붙는다(`lifespan()` 안에서 MCP 세션 매니저를 돌림).
`calls` 에 받은 도구 호출을 남긴다(「채점 경로 허브 호출 0」 같은 시험용).
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from clinic_chat.quality.raw_tools import RawStore, RawToolError
from tests.fake_hub import gold

PREFIX = "/procedure-hub"
BASE = "http://fake-hub"  # ASGI 전용 가짜 호스트(포트 안 엶)
GOLD_MCP_URL = BASE + PREFIX + "/gold-mcp/mcp"
RAW_MCP_URL = BASE + PREFIX + "/raw-mcp/mcp"
REST_URL = BASE + PREFIX + "/gold"
_SEC = TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _csv(v: str | None) -> list[str] | None:
    """허브 REST 목록 쿼리 꼴 — 쉼표로 이음."""
    return [x for x in v.split(",") if x] if v else None


class FakeHub:
    def __init__(self, raw_dir: str) -> None:
        self.store = RawStore(raw_dir)
        self.calls: list[tuple[str, str, dict]] = []  # (via, tool, args)
        self.fail_tools: set[str] = set()
        self.gold_mcp = self._gold_mcp()
        self.raw_mcp = self._raw_mcp()
        self.app = self._app()

    def _rec(self, via: str, tool: str, args: dict) -> None:
        self.calls.append((via, tool, args))
        if tool in self.fail_tools:
            raise ValueError(f"fixture 고장: {tool}")

    def _mcp_run(self, via: str, tool: str, args: dict, fn):
        """도메인 오류 → 같은 문구의 MCP tool 오류(SPEC-005 §5-0)."""
        try:
            self._rec(via, tool, args)
            return fn(**args)
        except (gold.GoldError, RawToolError, ValueError) as e:
            raise ToolError(str(e)) from e

    def _gold_mcp(self) -> MCPServer:
        m = MCPServer("procedure-hub-gold")
        hub = self

        def reg(fn):
            name = fn.__name__
            core = gold.TOOLS[name]

            def wrapped(**kw):
                return hub._mcp_run("gold-mcp", name, kw, core)

            wrapped.__name__ = name
            wrapped.__signature__ = __import__("inspect").signature(core)
            wrapped.__annotations__ = {**core.__annotations__, "return": dict[str, Any]}
            m.add_tool(wrapped, name=name, description=f"fixture {name}")

        for fn in gold.TOOLS.values():
            reg(fn)
        return m

    def _raw_mcp(self) -> MCPServer:
        m = MCPServer("procedure-hub-raw")
        hub = self

        @m.tool(name="raw_list", description="원장 파일 목록 · 키 구조")
        def raw_list(file: str | None = None, depth: int = 2) -> dict[str, Any]:
            return hub._mcp_run("raw-mcp", "raw_list", {"file": file, "depth": depth}, hub.store.raw_list)

        @m.tool(name="raw_get", description="경로 원문 값")
        def raw_get(path: str, offset: int = 0, max_bytes: int | None = None) -> dict[str, Any]:
            return hub._mcp_run("raw-mcp", "raw_get", {"path": path, "offset": offset, "max_bytes": max_bytes},
                                hub.store.raw_get)

        @m.tool(name="raw_search", description="원장 검색(공백 · 대소문자만)")
        def raw_search(query: str, files: list[str] | None = None, limit: int = 20,
                       snippet_chars: int = 80) -> dict[str, Any]:
            return hub._mcp_run("raw-mcp", "raw_search", {"query": query, "files": files, "limit": limit,
                                                          "snippet_chars": snippet_chars}, hub.store.raw_search)

        return m

    def _app(self) -> FastAPI:
        app = FastAPI()
        hub = self
        g = PREFIX + "/gold"

        def run(tool: str, **kw):
            try:
                hub._rec("gold-rest", tool, kw)
                return gold.TOOLS[tool](**kw)
            except (gold.GoldError, RawToolError, ValueError) as e:
                return JSONResponse({"detail": str(e)}, status_code=422)

        @app.get(g + "/health")
        def health():
            return {"build": gold.BUILD, "raw": {"files": 21}}

        @app.get(g + "/vocab")
        def vocab(kinds: str | None = None, sections: str | None = None, intent: str | None = None):
            return run("vocab", kinds=_csv(kinds), sections=_csv(sections), intent=intent)

        @app.get(g + "/resolve")
        def resolve(text: str, kinds: str | None = None, limit_per_span: int = 20, sections: str | None = None):
            return run("resolve", text=text, kinds=_csv(kinds), limit_per_span=limit_per_span, sections=_csv(sections))

        @app.post(g + "/recommend")
        async def recommend(req: Request):
            return run("recommend", **(await req.json()))

        @app.get(g + "/procedures/{pid}")
        def card(pid: str, product: str | None = None, sections: str | None = None, full: bool = False,
                 intent: str | None = None):
            return run("procedure_card", id=pid, product=product, sections=_csv(sections), full=full, intent=intent)

        @app.get(g + "/procedures/{pid}/checks")
        def checks(pid: str, conditions: str | None = None, sections: str | None = None, intent: str | None = None):
            return run("check_conditions", id=pid, conditions=_csv(conditions), sections=_csv(sections), intent=intent)

        @app.post(g + "/pairs")
        async def pairs(req: Request):
            return run("pair_check", **(await req.json()))

        @app.post(g + "/compare")
        async def comp(req: Request):
            return run("compare", **(await req.json()))

        @app.get(g + "/combos")
        def combos(concern: str | None = None, purpose: str | None = None, procedure: str | None = None,
                   menu: str | None = None, sections: str | None = None, intent: str | None = None):
            return run("combos", concern=concern, purpose=purpose, procedure=procedure, menu=menu,
                       sections=_csv(sections), intent=intent)

        @app.get(g + "/nodes/{nid}/neighbors")
        def nb(nid: str, depth: int = 1, limit: int = 50, sections: str | None = None):
            return run("neighbors", id=nid, depth=depth, limit=limit, sections=_csv(sections))

        app.mount(PREFIX + "/gold-mcp", self.gold_mcp.streamable_http_app(
            streamable_http_path="/mcp", json_response=True, stateless_http=True, transport_security=_SEC))
        app.mount(PREFIX + "/raw-mcp", self.raw_mcp.streamable_http_app(
            streamable_http_path="/mcp", json_response=True, stateless_http=True, transport_security=_SEC))
        return app

    @asynccontextmanager
    async def lifespan(self):
        async with AsyncExitStack() as st:
            await st.enter_async_context(self.gold_mcp.session_manager.run())
            await st.enter_async_context(self.raw_mcp.session_manager.run())
            yield self

    async def start(self) -> None:
        """세션 매니저를 따로 한 task 에서 돌림(들어간 task 에서 나와야 하는 anyio 규칙)."""
        self._ready, self._stop = asyncio.Event(), asyncio.Event()

        async def hold():
            async with self.lifespan():
                self._ready.set()
                await self._stop.wait()

        self._task = asyncio.create_task(hold())
        await self._ready.wait()

    async def stop(self) -> None:
        self._stop.set()
        await self._task
