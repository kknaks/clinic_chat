"""허브 골드 REST 클라이언트(SPEC-005 §5-1 · 접두 `/procedure-hub/gold`) — case3 의 ① · ③ · 시작 때 T9 vocab."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx2


class HubRestError(RuntimeError):
    pass


@dataclass
class HubRest:
    base_url: str
    http_client_factory: Any = None

    def _client(self) -> httpx2.AsyncClient:
        if self.http_client_factory:
            return self.http_client_factory()
        return httpx2.AsyncClient(timeout=30)

    async def _send(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> httpx2.Response:
        url = self.base_url.rstrip("/") + path
        # 목록 쿼리는 쉼표로(허브 REST 꼴 — `sections=overview,ops` · 반복 키는 마지막만 남음)
        q = {k: (",".join(v) if isinstance(v, list) else v) for k, v in (params or {}).items() if v is not None}
        async with self._client() as c:
            return await c.request(method, url, params=q, json=json)

    async def _req(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> dict:
        r = await self._send(method, path, params=params, json=json)
        if r.status_code >= 400:
            raise HubRestError(f"{method} {path} → {r.status_code} {r.text[:300]}")
        return r.json()

    async def timed(self, method: str, path: str, **kw) -> tuple[dict, float]:
        t = time.perf_counter()
        d = await self._req(method, path, **kw)
        return d, (time.perf_counter() - t) * 1000

    async def health(self) -> dict:
        return await self._req("GET", "/health")

    # 도구(T1~T11) — 입력 이름은 SPEC-005 그대로
    async def vocab(self, kinds: list[str] | None = None) -> dict:
        return await self._req("GET", "/vocab", params={"kinds": kinds})

    async def resolve(self, text: str, kinds: list[str] | None = None, limit_per_span: int | None = None) -> dict:
        return await self._req("GET", "/resolve", params={"text": text, "kinds": kinds, "limit_per_span": limit_per_span})

    async def peek(self, node: str, intent: str | None = None, include_conditional: bool | None = None) -> dict:
        """T10(SPEC-005 §5-16) — slim 노드 · 이웃 종류별 · section 목록 · 안전 요약. intent 는 허브가 받기만(M-3)."""
        r = route("peek", {"id": node, "intent": intent, "include_conditional": include_conditional})
        return await self._req(r[0], r[1], **_kw(r))

    async def expand(self, node: str, *, sections: list[str] | None = None, neighbors: list[str] | None = None,
                     neighbor_kinds: list[str] | None = None, fact_ids: list[str] | None = None,
                     intent: str | None = None) -> dict:
        """T11(SPEC-005 §5-16) — 고른 section 원문 · 고른 이웃 slim 카드만."""
        r = route("expand", {"id": node, "sections": sections, "neighbors": neighbors, "neighbor_kinds": neighbor_kinds,
                             "fact_ids": fact_ids, "intent": intent})
        return await self._req(r[0], r[1], **_kw(r))

    async def call(self, tool: str, args: dict) -> dict:
        """의도 경로(③)용 — 도구 이름 + 입력 → REST."""
        r = route(tool, args)
        return await self._req(r[0], r[1], **_kw(r))

    async def call_status(self, tool: str, args: dict) -> tuple[int, str]:
        """기록 검증(verify_runs)용 — 같은 입력 재호출 · (상태 코드, 응답 글 그대로)."""
        r = route(tool, args)
        resp = await self._send(r[0], r[1], **_kw(r))
        return resp.status_code, resp.text


def _kw(r: tuple[str, str, dict | None, Any]) -> dict:
    return {"params": r[2], "json": r[3]}


GET_PATHS = {"procedure_card": "/procedures/{id}", "check_conditions": "/procedures/{id}/checks",
             "neighbors": "/nodes/{id}/neighbors", "peek": "/nodes/{id}/peek", "combos": "/combos",
             "resolve": "/resolve", "vocab": "/vocab"}
POST_PATHS = {"recommend": "/recommend", "pair_check": "/pairs", "compare": "/compare", "expand": "/nodes/{id}/expand"}


def route(tool: str, args: dict) -> tuple[str, str, dict | None, Any]:
    """도구 이름 + 입력 → (method, path, query, body). id 는 경로로(POST expand 도) · 나머지는 query · body
    (body 는 넘긴 그대로 — None 칸도 · expand 는 None 뺌 · query 의 None 은 _send 가 뺌)."""
    a = dict(args or {})
    if tool == "expand":
        a = {k: v for k, v in a.items() if v is not None}
    tpl = GET_PATHS.get(tool) or POST_PATHS.get(tool)
    if tpl is None:
        raise HubRestError(f"없는 도구: {tool}")
    if "{id}" in tpl:
        if "id" not in a:
            raise HubRestError(f"{tool}: id 없음")
        tpl = tpl.replace("{id}", str(a.pop("id")))
    if tool in POST_PATHS:
        return "POST", tpl, None, a
    return "GET", tpl, a, None
