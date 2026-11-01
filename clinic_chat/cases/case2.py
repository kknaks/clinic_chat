"""case2 — 골드 MCP 9 도구 + Sonnet(SPEC-006 §3-3). `claude_contract` 는 도구 결과에 든 그대로 간다."""

from __future__ import annotations

from collections.abc import Callable

from clinic_chat import prompts
from clinic_chat.cases.loop import new_case_result, run_tool_loop
from clinic_chat.deps import NO_KEY, Deps
from clinic_chat.llm import CLI_PROVIDERS, cli
from clinic_chat.llm.cli import McpAttach
from clinic_chat.llm.specs import answer_spec
from clinic_chat.timing import CaseTimer


async def run(deps: Deps, question: str, timer: CaseTimer, on_text: Callable[[str], None],
              provider: str = "sdk") -> dict:
    if provider in CLI_PROVIDERS:  # CLI 가 허브 MCP 를 직접 붙여 도구 루프를 돎(앱은 이벤트만 받음)
        res = await cli.run(answer_spec(deps.settings, provider), system=prompts.system_for("case2"), prompt=question,
                            timer=timer, on_text=on_text, mcp=McpAttach("hub", url=deps.settings.hub_gold_mcp_url,
                                                               intent_tools=frozenset(deps.intent_tools),
                                                               tools=tuple(deps.startup.get("gold_tools") or ())),
                            collect_node_ids=True)
        return res
    if deps.anthropic is None:
        r = new_case_result()
        r["error"] = NO_KEY
        return r
    meta: dict = {}

    def seen(_name, r) -> None:
        if isinstance(r.data, dict) and isinstance(r.data.get("build"), dict):
            meta.setdefault("gold_build_hash", r.data["build"].get("build_hash"))

    async with deps.gold_mcp.session() as s:
        tools = await s.list_tools()
        res = await run_tool_loop(
            client=deps.anthropic, model=deps.settings.answer_model, system=prompts.system_for("case2"),
            question=question, tools=tools, call_tool=s.call, timer=timer, on_text=on_text,
            loop_max=deps.settings.case_tool_loop_max, collect_node_ids=True, on_tool_result=seen,
            tool_result_max_bytes=deps.settings.tool_result_max_bytes, intent_tools=deps.intent_tools,
        )
    res["trace"]["gold_build_hash"] = meta.get("gold_build_hash")
    return res
