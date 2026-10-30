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

    async def _req(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> dict:
        url = self.base_url.rstrip("/") + path
        q = {k: v for k, v in (params or {}).items() if v is not None}
        async with self._client() as c:
            r = await c.request(method, url, params=q, json=json)
        if r.status_code >= 400:
            raise HubRestError(f"{method} {path} → {r.status_code} {r.text[:300]}")
        return r.json()

    async def timed(self, method: str, path: str, **kw) -> tuple[dict, float]:
        t = time.perf_counter()
        d = await self._req(method, path, **kw)
        return d, (time.perf_counter() - t) * 1000

    # 도구(T1~T9) — 입력 이름은 SPEC-005 그대로
    async def health(self) -> dict:
        return await self._req("GET", "/health")

    async def vocab(self, kinds: list[str] | None = None) -> dict:
        return await self._req("GET", "/vocab", params={"kinds": kinds})

    async def resolve(self, text: str, kinds: list[str] | None = None, limit_per_span: int | None = None) -> dict:
        return await self._req("GET", "/resolve", params={"text": text, "kinds": kinds, "limit_per_span": limit_per_span})

    async def call(self, tool: str, args: dict) -> dict:
        """의도 경로(③)용 — 도구 이름 + 입력 → REST."""
        a = dict(args)
        if tool == "recommend":
            return await self._req("POST", "/recommend", json=a)
        if tool == "procedure_card":
            pid = a.pop("id")
            return await self._req("GET", f"/procedures/{pid}", params=a)
        if tool == "pair_check":
            return await self._req("POST", "/pairs", json=a)
        if tool == "check_conditions":
            pid = a.pop("id")
            return await self._req("GET", f"/procedures/{pid}/checks", params=a)
        if tool == "compare":
            return await self._req("POST", "/compare", json=a)
        if tool == "combos":
            return await self._req("GET", "/combos", params=a)
        if tool == "neighbors":
            nid = a.pop("id")
            return await self._req("GET", f"/nodes/{nid}/neighbors", params=a)
        if tool == "resolve":
            return await self._req("GET", "/resolve", params=a)
        if tool == "vocab":
            return await self._req("GET", "/vocab", params=a)
        raise HubRestError(f"없는 도구: {tool}")
