"""case1 · case2 공통 도구 루프(SPEC-006 §3-2 · R-2).

Sonnet 스트림 → `tool_use` 면 MCP 호출(병렬 호출은 모아 한 user 메시지로 결과) → 다시 Sonnet … `end_turn` 까지.
상한 `CASE_TOOL_LOOP_MAX` 번 Sonnet 호출 — 닿으면 `tool_choice: none` 으로 마지막 답 1번 · `loop_capped`.
강제 tool_choice(any · tool)는 쓰지 않는다 · 폴백 없음 · refusal 은 그 열에 기록.
"""

from __future__ import annotations

import asyncio
import collections
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from clinic_chat.clients.anthropic_client import answer_params, block_to_param, stream_once, usage_dict
from clinic_chat.clients.hub_mcp import ToolResult
from clinic_chat.config import TRUNCATION_MARKERS
from clinic_chat.pricing import cost_usd
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


VALID_INTENTS = frozenset(f"I-{i:02d}" for i in range(1, 13))


# 허브 MCP 도구 짧은 이름(골드 11 · 원장 3) — stray 나눔(SPEC-006 §3-8 막는 법 2)
HUB_TOOL_NAMES = ("resolve", "recommend", "procedure_card", "pair_check", "check_conditions", "compare", "combos",
                  "neighbors", "vocab", "peek", "expand", "raw_list", "raw_get", "raw_search")


def stray_kind(name: str) -> str:
    """`bare_hub` = 접두만 빠진 허브 도구 이름(짧은 이름이거나 그것으로 시작 · 예 check_conditions_placeholder) ·
    `foreign` = 그 밖(내장 · 다른 서버 `mcp__x__…` · 지어낸 이름)."""
    n = str(name or "")
    if n.startswith("mcp__"):
        return "foreign"
    return "bare_hub" if any(n == t or n.startswith(t) for t in HUB_TOOL_NAMES) else "foreign"


def mark_call(call: dict, *, intent_tools: Iterable[str], stray: bool, name: str | None = None) -> dict:
    """호출 열 때 아는 칸(SPEC-006 §3-6 · §4-2 · §3-8) — `intent_passed`(스키마에 intent 있는 도구만 · I-01~I-12 일 때만 true)
    · `stray`(허브 MCP 밖 도구) · `stray_kind`(stray 일 때 · name = CLI 가 받은 이름 그대로)."""
    inp = call.get("input")
    if call["tool"] in set(intent_tools) and not stray:
        call["intent_passed"] = isinstance(inp, dict) and inp.get("intent") in VALID_INTENTS
    else:
        call["intent_passed"] = None
    call["stray"] = stray
    if stray:
        call["stray_kind"] = stray_kind(name if name is not None else call["tool"])
    return call


def mark_result(call: dict, text: str | None, provider: str) -> dict:
    """도구 결과를 받은 뒤 칸(§3-5) — `delivered_bytes`(모델이 받은 바이트 · `output_bytes` 같은 값 · 호환)
    · `truncated`(provider 잘림 표시가 결과 글에 있음)."""
    call["output_bytes"] = len(text.encode("utf-8")) if text is not None else None
    call["delivered_bytes"] = call["output_bytes"]
    call["truncated"] = bool(text) and any(m in text for m in TRUNCATION_MARKERS.get(provider, ()))
    return call


def summarize_calls(trace: dict) -> dict:
    """판 단위 칸 — `intent_ratio` · `intent_values{}`(넘긴 값 그대로 · 틀린 값 포함) · `stray_tools`."""
    calls = trace.get("tool_calls") or []
    elig = [c for c in calls if c.get("intent_passed") is not None]
    trace["intent_ratio"] = round(sum(1 for c in elig if c["intent_passed"]) / len(elig), 4) if elig else None
    vals = collections.Counter(str(c["input"]["intent"]) for c in elig
                               if isinstance(c.get("input"), dict) and "intent" in c["input"])
    trace["intent_values"] = dict(sorted(vals.items()))
    trace["stray_tools"] = sum(1 for c in calls if c.get("stray"))
    by = {"bare_hub": 0, "foreign": 0}
    for i, c in enumerate(calls):
        if not c.get("stray"):
            continue
        by[c.get("stray_kind") or "foreign"] += 1
        nxt = calls[i + 1] if i + 1 < len(calls) else None  # 바로 다음 호출이 같은 도구의 제 이름(접두)인가
        c["stray_fixed_next"] = bool(nxt and not nxt.get("stray") and
                                     (nxt["tool"] == c["tool"] or str(c["tool"]).startswith(str(nxt["tool"]))))
    trace["stray_by_kind"] = by
    return trace


def cap_text(text: str, max_bytes: int) -> tuple[str, bool]:
    """도구 결과가 크면 앞부분만 + 잘림 표시(SDK 판 입력 상한 · 0 = 없음 — SPEC-006 v2 §2-5)."""
    b = text.encode("utf-8")
    if max_bytes <= 0 or len(b) <= max_bytes:
        return text, False
    head = b[:max_bytes].decode("utf-8", errors="ignore")
    return (head + f"\n…[잘림: 결과 {len(b):,}바이트 중 앞 {max_bytes:,}바이트만 — 원장은 raw_get 의 path · offset · "
                   f"max_bytes 로 나눠 읽고, 골드는 sections 로 필요한 칸만 다시 요청]"), True


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
    tool_result_max_bytes: int = 0,
    intent_tools: Iterable[str] = (),
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
        params = answer_params(model, system, messages, tools, {"type": "none"} if capped else None, loop_cache=True)
        round_first: list[float] = []

        def _txt(t: str, round_first: list[float] = round_first) -> None:
            ms = timer.mark_text()
            if not round_first:
                round_first.append(ms)
            res["answer"] += t
            on_text(t)

        name = f"claude#{n}"
        with timer.stage(name) as st:
            final = await stream_once(client, params, _txt)
            st["tokens"] = usage_dict(final.usage)
            st["cost_usd"] = cost_usd(model, st["tokens"])
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
            call = mark_call({"tool": b.name, "input": b.input, "ms": round(r.ms, 1), "is_error": r.is_error},
                             intent_tools=intent_tools, stray=False)  # SDK 판은 준 도구만 부를 수 있음
            if collect_node_ids:
                call["node_ids"] = node_ids(r.data) if r.data is not None else []
            text, cut = cap_text(r.text or "(빈 결과)", tool_result_max_bytes)
            mark_result(call, text, "sdk")
            if cut:
                call["truncated_to"] = tool_result_max_bytes
            tool_calls.append(call)
            if on_tool_result:
                on_tool_result(b.name, r)
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": r.is_error})
        messages.append({"role": "user", "content": results})
    timer.finish()
    res["timing"] = timer.as_dict()
    res["tokens"] = tokens
    res["trace"] = summarize_calls({"tool_calls": tool_calls})
    res["cost"] = case_cost(timer.stages)
    return res


def case_cost(stages: list[dict]) -> dict:
    """열 비용 — 단계마다 cost_usd 합(Sonnet · Jev)."""
    by = {s["name"]: s["cost_usd"] for s in stages if s.get("cost_usd") is not None}
    return {"usd": round(sum(by.values()), 6), "by_stage": by}


def dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)
