"""case1 · case2 공통 도구 루프(SPEC-006 §3-2 · R-2).

Sonnet 스트림 → `tool_use` 면 MCP 호출(병렬 호출은 모아 한 user 메시지로 결과) → 다시 Sonnet … `end_turn` 까지.
상한 `CASE_TOOL_LOOP_MAX` 번 Sonnet 호출 — 닿으면 `tool_choice: none` 으로 마지막 답 1번 · `loop_capped`.
강제 tool_choice(any · tool)는 쓰지 않는다 · 폴백 없음 · refusal 은 그 열에 기록.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from clinic_chat.clients.anthropic_client import answer_params, block_to_param, stream_once, usage_dict
from clinic_chat.clients.hub_mcp import ToolResult
from clinic_chat.timing import CaseTimer

NODE_KINDS = ("concern", "site", "purpose", "check_condition", "technique", "family", "unit_procedure",
              "product", "package", "combo", "menu", "intent", "ledger")
_NODE_RE = re.compile(r"^(?:" + "|".join(NODE_KINDS) + r"):\S.*$")


def node_ids(data: Any) -> list[str]:
    """결과 JSON 안의 정본 id(`{kind}:{키}` 꼴) 전부 — 처음 나온 순서."""
    seen: dict[str, None] = {}

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and _NODE_RE.match(v):
            seen.setdefault(v, None)

    walk(data)
    return list(seen)


def new_case_result() -> dict:
    return {"answer": "", "stop_reason": None, "loop_capped": False, "error": None, "refusal": False,
            "timing": {}, "tokens": {}, "trace": {}}


async def run_tool_loop(
    *,
    client: Any,
    model: str,
    system: str,
    question: str,
    tools: list[dict],
    call_tool: Callable[[str, dict], Awaitable[ToolResult]],
    timer: CaseTimer,
    on_text: Callable[[str], None],
    loop_max: int,
    collect_node_ids: bool = False,
    on_tool_result: Callable[[str, ToolResult], None] | None = None,
) -> dict:
    res = new_case_result()
    messages: list[dict] = [{"role": "user", "content": question}]
    tool_calls: list[dict] = []
    tokens: dict[str, dict] = {}
    n_tool = 0
    n = 0
    while True:
        n += 1
        capped = n > loop_max
        params = answer_params(model, system, messages, tools, {"type": "none"} if capped else None)
        round_first: list[float] = []

        def _txt(t: str) -> None:
            ms = timer.mark_text()
            if not round_first:
                round_first.append(ms)
            res["answer"] += t
            on_text(t)

        name = f"claude#{n}"
        with timer.stage(name) as st:
            final = await stream_once(client, params, _txt)
            st["tokens"] = usage_dict(final.usage)
        timer.llm_round_trips += 1
        tokens[name] = st["tokens"]
        timer.ttft_answer_ms = round_first[0] if round_first else None
        res["stop_reason"] = final.stop_reason
        if final.stop_reason == "refusal":
            res["refusal"] = True
            break
        uses = [b for b in final.content if b.type == "tool_use"]
        if final.stop_reason != "tool_use" or not uses or capped:
            if capped:
                res["loop_capped"] = True
            break
        messages.append({"role": "assistant", "content": [block_to_param(b) for b in final.content]})

        async def one(b: Any, k: int) -> tuple[Any, ToolResult]:
            with timer.stage(f"tool:{b.name}#{k}"):
                r = await call_tool(b.name, dict(b.input or {}))
            return b, r

        ks = []
        for _ in uses:
            n_tool += 1
            ks.append(n_tool)
        done = await asyncio.gather(*(one(b, k) for b, k in zip(uses, ks)))
        results = []
        for b, r in done:
            call = {"tool": b.name, "input": b.input, "output_bytes": len(r.text.encode("utf-8")),
                    "ms": round(r.ms, 1), "is_error": r.is_error}
            if collect_node_ids:
                call["node_ids"] = node_ids(r.data) if r.data is not None else []
            tool_calls.append(call)
            if on_tool_result:
                on_tool_result(b.name, r)
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": r.text or "(빈 결과)",
                            "is_error": r.is_error})
        messages.append({"role": "user", "content": results})
    timer.finish()
    res["timing"] = timer.as_dict()
    res["tokens"] = tokens
    res["trace"] = {"tool_calls": tool_calls}
    return res


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)
