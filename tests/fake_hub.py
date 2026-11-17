"""모의 허브 — **정해 둔 응답을 그대로 돌려주는 것**(쪽 넘김 · 오류 · 머리 꼴)까지만. 규칙을 계산하지 않는다(WORK-002 §11).

응답 예는 SPEC-001 §4 의 필드 표로 손으로 적은 **가짜 코드 · 가짜 이름**이다(ZZ… · 「가짜…」).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx2

from clinic_chat.clients.hub_rest import OUTSIDE_LISTS, PAGE_LIST, TOOL_PATHS, HubRest

BUILD = {"gold_hash": "fakehash", "aux_hash": "fakeaux", "loaded_at": "2026-11-16T00:00:00+09:00"}
PATH_TOOL = {v: k for k, v in TOOL_PATHS.items()}


def row(table: str, gid: str, **kw: Any) -> dict:
    return {"gold_table": table, "gid": gid, "evidence_kind": "정리", **kw}


class FakeHub:
    """`routes[tool]` = callable(params) → 응답 본문(쪽 넘김 전 전체) 또는 (status, body). 쪽 넘김은 여기서 자른다."""

    def __init__(self, routes: dict[str, Any] | None = None, *, limit_cap: int = 2, fail_on: set[str] | None = None):
        self.routes = routes or {}
        self.routes.setdefault("scope", {"holders": []})  # 0.6 PR1 무리 판정 — 길을 안 준 시험은 「묶을 것 없음」
        self.limit_cap = limit_cap
        self.fail_on = fail_on or set()
        self.requests: list[tuple[str, dict]] = []

    def parse(self, request: httpx2.Request) -> tuple[str, dict]:
        u = urlsplit(str(request.url))
        tool = PATH_TOOL.get("/" + u.path.rstrip("/").rsplit("/", 1)[-1], "?")
        params: dict[str, Any] = {}
        for k, v in parse_qsl(u.query, keep_blank_values=True):
            if k == "filters":
                params.setdefault("filters", {}).update([v.split("=", 1)])
            else:
                params[k] = v
        return tool, params

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        tool, params = self.parse(request)
        self.requests.append((tool, params))
        if tool in self.fail_on and params.get("cursor"):
            return httpx2.Response(500, json={"detail": "internal: fixture"})
        fn = self.routes.get(tool)
        if fn is None:
            return httpx2.Response(404, json={"detail": f"not_found: {tool}"})
        got = fn(params) if callable(fn) else fn
        if isinstance(got, tuple):
            return httpx2.Response(got[0], json=got[1])
        body = json.loads(json.dumps(got))
        body.setdefault("build", BUILD)
        name = PAGE_LIST.get(tool, "rows")
        if tool in PAGE_LIST and isinstance(body.get(name), list) and "total" not in body:
            items = body[name]
            lim = min(int(params.get("limit") or self.limit_cap), self.limit_cap)
            start = int(params.get("cursor") or 0)
            body[name] = items[start:start + lim]
            body["total"] = len(items)
            body["next_cursor"] = str(start + lim) if start + lim < len(items) else None
            if start:
                for o in OUTSIDE_LISTS.get(tool, ()):
                    body.pop(o, None)
        body.setdefault("next_cursor", None)
        body.setdefault("request", params)
        return httpx2.Response(200, json=body)

    def client(self, page_limit: int = 2) -> HubRest:
        def factory() -> httpx2.AsyncClient:
            return httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle), timeout=5)

        return HubRest("http://fake-hub/procedure-hub/gold", http_client_factory=factory, page_limit=page_limit)
