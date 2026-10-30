"""case1 — 원장 MCP 3 도구 + Sonnet(SPEC-006 §3-2)."""

from __future__ import annotations

from collections.abc import Callable

from clinic_chat import prompts
from clinic_chat.cases.loop import new_case_result, run_tool_loop
from clinic_chat.deps import NO_KEY, Deps
from clinic_chat.timing import CaseTimer


async def run(deps: Deps, question: str, timer: CaseTimer, on_text: Callable[[str], None]) -> dict:
    if deps.anthropic is None:
        r = new_case_result()
        r["error"] = NO_KEY
        return r
    meta: dict = {}

    def seen(_name, r) -> None:
        if isinstance(r.data, dict) and isinstance(r.data.get("raw"), dict):
            meta.setdefault("raw_manifest_sha16", r.data["raw"].get("dir_manifest_sha16"))

    async with deps.raw_mcp.session() as s:
        tools = await s.list_tools()
        res = await run_tool_loop(
            client=deps.anthropic, model=deps.settings.answer_model, system=prompts.system_for("case1"),
            question=question, tools=tools, call_tool=s.call, timer=timer, on_text=on_text,
            loop_max=deps.settings.case_tool_loop_max, on_tool_result=seen,
        )
    res["trace"]["context"] = res["trace"]["tool_calls"]
    res["trace"]["raw_manifest_sha16"] = meta.get("raw_manifest_sha16")
    return res
