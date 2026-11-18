"""A 열 — LLM + 원장 로컬 stdio MCP 3(SPEC-002 §2-1 · §2-3 · §7-3 A trace)."""

from __future__ import annotations

import json

from clinic_chat.columns.base import ColumnOut, Deps, cited_raw, column, llm_input_bytes, llm_tokens
from clinic_chat.llm import cli
from clinic_chat.llm.cli import McpAttach
from clinic_chat.raw_ledger.raw_mcp import SERVER_NAME
from clinic_chat.raw_ledger.raw_tools import paths_of
from clinic_chat.timing import Timer


def attach(d: Deps) -> McpAttach:
    return McpAttach(SERVER_NAME, command=d.python, args=["-m", "clinic_chat.raw_ledger.raw_mcp"],
                     env={"RAW_DIR": d.settings.raw_dir or "", "PYTHONPATH": d.root})


def trace_calls(tool_calls: list[dict]) -> list[dict]:
    out = []
    for c in tool_calls:
        try:
            resp = json.loads(c.get("output_text") or "")
        except ValueError:
            resp = None
        resp = resp if isinstance(resp, dict) else None
        out.append({"tool": c["tool"], "input": c.get("input"), "output_bytes": c.get("output_bytes"),
                    "paths": paths_of(c["tool"], c.get("input") or {}, resp),
                    "truncated": bool(resp.get("truncated")) if resp else None, "is_error": c.get("is_error")})
    return out


async def run(question: str, d: Deps, effort: str | None = None) -> ColumnOut:
    if not d.settings.raw_dir:
        return ColumnOut(column(error={"kind": "config", "stage": "a", "detail": "RAW_DIR 없음"},
                                trace={"tool_calls": []}))
    timer = Timer()
    r = await cli.run(d.spec("on", effort), system=d.system("a"), prompt=question, mcp=attach(d), timer=timer,
                      max_concurrent=d.settings.cli_max_concurrent)
    calls = trace_calls(r["tool_calls"])
    res = column(r["answer"], timing=r["timing"], tokens=llm_tokens(r),
                 calls={"llm_round_trips": r["llm_round_trips"], "tool_calls": len(calls)},
                 cited=cited_raw(r["answer"]),
                 error={"kind": "cli", "stage": "llm", "detail": r["error"]} if r["error"] else None,
                 trace={"tool_calls": calls, "interim_text": r["interim_text"],
                        "llm_input_bytes": llm_input_bytes(question, calls), "tokens_result": r.get("tokens_result")})
    return ColumnOut(res, r["observed_model"])
