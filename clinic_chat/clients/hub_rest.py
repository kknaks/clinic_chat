"""허브 gold REST 클라이언트(SPEC-001 §4 · 접두 `HUB_REST_URL` = `…/procedure-hub/gold`).

- 도구 9 = 경로 9(전부 GET · `coverage` 는 0.5.0 — SPEC-001 §4-9 · 쪽 넘김 없음 · 노드 64 넘으면 앱이 나눠 부른다). 목록 입력은 콤마 · `filters` 는 `칸=값` 되풀이(WORK-001 O-5 · H-5).
- **`read_all`** — `next_cursor: null` 까지 넘겨 그 조회 전부를 모은다. 「읽은 쪽 단위 수 ≠ `total`」 이면 `complete = false`.
  응답 `build` 가 도중에 바뀌면 오류(cursor 는 build 에 묶임 — SPEC-001 §4-0).
- 허브 오류는 본문 문구가 아니라 **HTTP 상태**(422 · 404 · 400)로 가른다(WORK-002 §3 · WORK-001 H-2). 문구는 기록에만.

실물 확인 E4:  uv run python -m clinic_chat.clients.hub_rest --ping
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx2

from clinic_chat.config import Settings, load_settings

TOOL_PATHS = {
    "resolve": "/resolve", "scope": "/scope", "neighbors": "/neighbors", "facets": "/facets",
    "concern_candidates": "/concern-candidates", "pair": "/pair", "evidence": "/evidence", "health": "/health",
    "coverage": "/coverage",
}
COVERAGE_NODES_MAX = 64  # 허브 상한(SPEC-001 §4-9 — 넘으면 `invalid_argument` · 잘라 내지 않는다)
# 쪽 단위(= total 단위) 목록 이름 · 쪽 밖 목록(첫 쪽에만) — SPEC-001 §4-0 「total 단위」 표
PAGE_LIST = {"resolve": "candidates", "scope": "holders", "neighbors": "items", "facets": "rows",
             "concern_candidates": "groups", "pair": "rows", "evidence": "refs"}
OUTSIDE_LISTS = {"concern_candidates": ("modality_only", "unreached"), "pair": ("same_day_packages",)}
ERROR_KIND = {422: "invalid_argument", 404: "not_found", 400: "cursor_invalid"}


class HubError(RuntimeError):
    def __init__(self, status: int | None, kind: str, message: str, tool: str = "") -> None:
        super().__init__(f"{tool}: {kind} ({status}) {message[:200]}")
        self.status, self.kind, self.message, self.tool = status, kind, message, tool


def encode_params(request: dict) -> list[tuple[str, str]]:
    """요청 dict → 쿼리. 목록 = 콤마 · filters(dict) = `filters=칸=값` 되풀이 · bool = true/false · None 은 뺌."""
    out: list[tuple[str, str]] = []
    for k, v in request.items():
        if v is None:
            continue
        if k == "filters":
            items = v.items() if isinstance(v, dict) else (x.split("=", 1) for x in v)
            for col, val in items:
                out.append(("filters", f"{col}={val}"))
        elif isinstance(v, list | tuple):
            out.append((k, ",".join(str(x) for x in v)))
        elif isinstance(v, bool):
            out.append((k, "true" if v else "false"))
        else:
            out.append((k, str(v)))
    return out


def page_units(tool: str, page: dict) -> int:
    """한 쪽의 쪽 단위 수. evidence 는 refs 를 편 줄(source_ref 0 인 참조는 1줄)."""
    if tool == "evidence":
        return sum(max(1, len(r.get("source_refs") or [])) for r in page.get("refs") or [])
    return len(page.get(PAGE_LIST.get(tool, "rows")) or [])


@dataclass
class ReadAll:
    tool: str
    request: dict
    total: int | None = None
    pages: int = 0
    read: int = 0
    complete: bool = False
    build: dict | None = None
    head: dict = field(default_factory=dict)  # 첫 쪽의 머리(counts · ledger_check · request …)
    items: list = field(default_factory=list)  # 쪽 단위 목록을 이은 것
    outside: dict = field(default_factory=dict)  # 쪽 밖 목록(첫 쪽)
    ledger_check: list = field(default_factory=list)
    error: str | None = None
    ms: float = 0.0
    cursors: list = field(default_factory=list)  # 쪽마다 요청 cursor(첫 쪽 None) — 방문 장부 키

    def summary(self) -> dict:
        """B trace `breadth[]` 한 줄(SPEC-002 §7-3)."""
        return {"tool": self.tool, "request": self.request, "total": self.total, "pages": self.pages,
                "read": self.read, "complete": self.complete, "error": self.error,
                "ledger_check": list(self.ledger_check)}  # 코드 검수 v052 W-3 — (가) · HG1 근거를 runs 로 되짚게

    def as_response(self) -> dict:
        """모은 것을 한 응답 꼴로(gid 모으기 · 키 스크립트가 같은 함수로 훑게)."""
        body = dict(self.head)
        body[PAGE_LIST.get(self.tool, "rows")] = self.items
        body.update(self.outside)
        body["ledger_check"] = self.ledger_check
        return body


@dataclass
class HubRest:
    base_url: str
    http_client_factory: Any = None
    page_limit: int = 100
    calls: int = 0
    log: list = field(default_factory=list)

    def _client(self) -> httpx2.AsyncClient:
        if self.http_client_factory:
            return self.http_client_factory()
        return httpx2.AsyncClient(timeout=30)

    async def _send(self, path: str, params: list[tuple[str, str]]) -> httpx2.Response:
        url = self.base_url.rstrip("/") + path
        async with self._client() as c:
            return await c.get(url, params=params)

    async def call(self, tool: str, request: dict | None = None) -> dict:
        """도구 한 번(한 쪽). 4xx · 5xx 는 HubError(상태로 종류를 가름)."""
        if tool not in TOOL_PATHS:
            raise HubError(None, "unknown_tool", tool, tool)
        self.calls += 1
        t = time.perf_counter()
        try:
            r = await self._send(TOOL_PATHS[tool], encode_params(request or {}))
        except httpx2.HTTPError as e:
            raise HubError(None, "transport", f"{type(e).__name__}: {e}", tool) from e
        ms = round((time.perf_counter() - t) * 1000, 1)
        self.log.append({"tool": tool, "status": r.status_code, "ms": ms})
        if r.status_code >= 400:
            raise HubError(r.status_code, ERROR_KIND.get(r.status_code, "http_error"), r.text, tool)
        try:
            return r.json()
        except ValueError as e:
            raise HubError(r.status_code, "bad_json", r.text, tool) from e

    async def read_all(self, tool: str, request: dict, *, max_pages: int = 10_000) -> ReadAll:
        """`next_cursor: null` 까지. 중간 오류면 `complete = false` · `error` (예외를 올리지 않는다 — 부르는 쪽이 판정)."""
        req = {k: v for k, v in request.items() if v is not None and k != "cursor"}
        req.setdefault("limit", self.page_limit)
        out = ReadAll(tool=tool, request={k: v for k, v in req.items() if k != "limit"})
        cursor: str | None = None
        t = time.perf_counter()
        try:
            while True:
                q = dict(req)
                if cursor:
                    q["cursor"] = cursor
                out.cursors.append(cursor)
                page = await self.call(tool, q)
                out.pages += 1
                if out.build is None:
                    out.build = page.get("build")
                    out.total = page.get("total")
                    out.head = {k: v for k, v in page.items()
                                if k not in (PAGE_LIST.get(tool, "rows"), *OUTSIDE_LISTS.get(tool, ()))}
                    for name in OUTSIDE_LISTS.get(tool, ()):
                        out.outside[name] = list(page.get(name) or [])
                    out.ledger_check = list(page.get("ledger_check") or [])
                elif _build_key(page.get("build")) != _build_key(out.build):
                    raise HubError(None, "build_changed", "쪽 넘김 도중 build 가 바뀜", tool)
                out.items.extend(page.get(PAGE_LIST.get(tool, "rows")) or [])
                out.read += page_units(tool, page)
                cursor = page.get("next_cursor")
                if not cursor:
                    break
                if out.pages >= max_pages:
                    raise HubError(None, "too_many_pages", f"{max_pages} 쪽 넘음", tool)
            out.complete = out.total is None or out.read == out.total
            if not out.complete:
                out.error = f"read {out.read} != total {out.total}"
        except HubError as e:
            out.complete = False
            out.error = f"{e.kind}: {e.message[:200]}"
            out.head.setdefault("error_status", e.status)
        out.ms = round((time.perf_counter() - t) * 1000, 1)
        return out

    # 도구 8 — 입력 이름은 SPEC-001 §4 그대로
    async def resolve(self, text: str | None = None, *, range: str | None = None, tables: list[str] | None = None,
                      cursor: str | None = None, limit: int | None = None) -> dict:
        return await self.call("resolve", {"text": text, "range": range, "tables": tables, "cursor": cursor,
                                           "limit": limit})

    async def scope(self, node: str, roles: list[str] | None = None, **kw) -> dict:
        return await self.call("scope", {"node": node, "roles": roles, **kw})

    async def neighbors(self, node_id: str, edge_types: list[str], direction: str | None = None, **kw) -> dict:
        return await self.call("neighbors", {"node_id": node_id, "edge_types": edge_types, "direction": direction, **kw})

    async def facets(self, facet: str, node: str | None = None, **kw) -> dict:
        return await self.call("facets", {"node": node, "facet": facet, **kw})

    async def concern_candidates(self, concern_code: str, site: str | None = None, **kw) -> dict:
        return await self.call("concern_candidates", {"concern_code": concern_code, "site": site, **kw})

    async def pair(self, a: str, b: str | None = None, **kw) -> dict:
        return await self.call("pair", {"a": a, "b": b, **kw})

    async def evidence(self, gold_table: str, gold_id: str, **kw) -> dict:
        return await self.call("evidence", {"gold_table": gold_table, "gold_id": gold_id, **kw})

    async def health(self) -> dict:
        return await self.call("health", {})

    async def coverage(self, nodes: list[str], kinds: list[str] | None = None) -> dict:
        """후보 × 정보 종류 × 층의 행 수(셈뿐). 64 를 넘으면 64 씩 나눠 부르고 `items` 를 후보 순서대로 잇는다."""
        items: list = []
        head: dict = {}
        for i in range(0, len(nodes), COVERAGE_NODES_MAX):
            part = nodes[i:i + COVERAGE_NODES_MAX]
            r = await self.call("coverage", {"nodes": part, "kinds": kinds})
            if not head:
                head = {k: v for k, v in r.items() if k != "items"}
            elif _build_key(r.get("build")) != _build_key(head.get("build")):
                raise HubError(None, "build_changed", "coverage 나눔 도중 build 가 바뀜", "coverage")
            items.extend(r.get("items") or [])
        return {**head, "items": items, "total": len(items), "next_cursor": None, "calls": -(-len(nodes) // COVERAGE_NODES_MAX)}


def _build_key(b: dict | None) -> tuple:
    b = b or {}
    return (b.get("gold_hash"), b.get("aux_hash"))


def collect_gids(obj: Any) -> list[str]:
    """응답 JSON 을 재귀로 훑어 **`gid` 라는 이름의 칸만** 모은다(순서 유지 · 중복 제거).

    `rows[]` · `groups[].rows[]` · 쪽 밖 목록 · `candidates[]` · `holders[]` 가 다 걸린다. `neighbors` 엣지의 `gold_id` 는
    이름이 `gid` 가 아니라 안 걸린다(WORK-002 §6 — `edge_gold_ids[]` 로 따로). B 의 `verified` · C 의 `gids[]` · 답 키가 같이 쓴다.
    """
    seen: dict[str, None] = {}

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            g = v.get("gid")
            if isinstance(g, str) and g:
                seen.setdefault(g, None)
            for x in v.values():
                if isinstance(x, dict | list):
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(obj)
    return list(seen)


def collect_edge_gold_ids(obj: Any) -> list[str]:
    """`neighbors` 응답의 `items[].gold_id` — 기록만(D2 · D7 재료 아님)."""
    out: dict[str, None] = {}
    for it in (obj or {}).get("items") or [] if isinstance(obj, dict) else []:
        if isinstance(it, dict) and it.get("gold_id"):
            out.setdefault(str(it["gold_id"]), None)
    return list(out)


async def _ping(s: Settings) -> dict:
    """E4 — health + 도구 한 건(쪽 넘김 끝까지) + 맞춤 꼴 · coverage 한 건의 키 집합(모의와 대조 — W-5) + MCP list_tools."""
    from clinic_chat.clients.hub_mcp import HubMCP

    hub = HubRest(s.hub_rest_url, page_limit=s.hub_page_limit)
    h = await hub.health()
    lst = await hub.read_all("resolve", {"range": "시술", "limit": 50})
    first = lst.items[0] if lst.items else {}
    m = await hub.resolve(first.get("text") or "")  # 맞춤 꼴 한 건(글 = 목록 꼴 첫 행의 text 그대로 — W-5)
    node = f"{first.get('entity_table')}:{first.get('entity_code')}"
    cov = await hub.coverage([node])
    mc = (m.get("candidates") or [{}])[0]
    ci = (cov.get("items") or [{}])[0]
    mcp = HubMCP(s.hub_gold_mcp_url)
    async with mcp.session() as sess:
        tools = await sess.list_tools()
    return {"ok": True, "gold_hash": (h.get("build") or {}).get("gold_hash"),
            "keys": {"resolve": sorted(m), "resolve.candidates[]": sorted(mc), "matched": sorted(mc.get("matched") or {}),
                     "coverage": sorted(cov.keys() - {"calls"}), "coverage.items[]": sorted(ci),
                     "coverage.items[].kinds[]": sorted((ci.get("kinds") or [{}])[0])},
            "coverage_kinds": len(ci.get("kinds") or []),
            "aux_hash": (h.get("build") or {}).get("aux_hash"),
            "read_all": {"tool": "resolve(목록 꼴 · range=시술)", "total": lst.total, "pages": lst.pages,
                         "read": lst.read, "complete": lst.complete, "error": lst.error},
            "mcp_tools": len(tools), "mcp_tool_names": sorted(t["name"] for t in tools)}


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.clients.hub_rest")
    ap.add_argument("--ping", action="store_true", help="E4 — 로컬 허브 health + 도구 한 건 + MCP list_tools")
    args = ap.parse_args()
    if not args.ping:
        ap.print_help()
        return
    try:
        print(json.dumps(asyncio.run(_ping(load_settings())), ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}, ensure_ascii=False))
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
