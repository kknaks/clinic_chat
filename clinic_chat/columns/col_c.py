"""C 열 — LLM + 허브 MCP 직접(http · 도구 7 = `health` 뺌 — O-15 (a) · SPEC-002 §2-4 · §7-3 C trace).

trace `gids[]` = 그 응답에 든 행의 `gid` 전부(B 의 verified 와 같은 함수 `collect_gids`). `neighbors` 엣지의 `gold_id` 는
`gids[]` 에 넣지 않고 `edge_gold_ids[]` 에만 적는다(WORK-002 §6 — D2 · D7 재료 아님). CLI 쪽에서 결과가 잘렸으면 표시.
"""

from __future__ import annotations

import json

from clinic_chat.clients.hub_rest import collect_edge_gold_ids, collect_gids
from clinic_chat.columns.base import ColumnOut, Deps, cited_gids, column, llm_input_bytes, llm_tokens
from clinic_chat.llm import cli
from clinic_chat.llm.cli import McpAttach
from clinic_chat.timing import Timer

SERVER = "hub"
TOOLS8 = ("resolve", "scope", "neighbors", "facets", "concern_candidates", "pair", "evidence", "coverage")


def attach(d: Deps) -> McpAttach:
    return McpAttach(SERVER, url=d.settings.hub_gold_mcp_url, tools=TOOLS8)


def trace_calls(tool_calls: list[dict]) -> list[dict]:
    out = []
    for c in tool_calls:
        try:
            resp = json.loads(c.get("output_text") or "")
        except ValueError:
            resp = None
        if isinstance(resp, dict) and set(resp) == {"result"}:
            resp = resp["result"]
        out.append({"tool": c["tool"], "input": c.get("input"), "output_bytes": c.get("output_bytes"),
                    "gids": collect_gids(resp) if resp is not None else [],
                    "edge_gold_ids": collect_edge_gold_ids(resp) if c["tool"] == "neighbors" else [],
                    "cli_truncated": c.get("cli_truncated"), "parsed": resp is not None, "is_error": c.get("is_error")})
    return out


async def run(question: str, d: Deps, effort: str | None = None) -> ColumnOut:
    timer = Timer()
    r = await cli.run(d.spec("on", effort), system=d.system("c"), prompt=question, mcp=attach(d), timer=timer,
                      max_concurrent=d.settings.cli_max_concurrent)
    calls = trace_calls(r["tool_calls"])
    res = column(r["answer"], timing=r["timing"], tokens=llm_tokens(r),
                 calls={"llm_round_trips": r["llm_round_trips"], "tool_calls": len(calls), "hub_calls": len(calls)},
                 cited=cited_gids(r["answer"]),
                 error={"kind": "cli", "stage": "llm", "detail": r["error"]} if r["error"] else None,
                 trace={"tool_calls": calls, "thinking_seen": r["thinking_seen"], "interim_text": r["interim_text"],
                        "llm_input_bytes": llm_input_bytes(question, calls), "tokens_result": r.get("tokens_result")})
    return ColumnOut(res, r["observed_model"])
