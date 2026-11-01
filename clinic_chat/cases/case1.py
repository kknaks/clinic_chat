"""case1 — 원장 MCP 3 도구 + Sonnet(SPEC-006 §3-2)."""

from __future__ import annotations

from collections.abc import Callable

from clinic_chat import prompts
from clinic_chat.cases.loop import new_case_result, run_tool_loop
from clinic_chat.deps import NO_KEY, Deps
from clinic_chat.llm import CLI_PROVIDERS, cli
from clinic_chat.llm.cli import McpAttach
from clinic_chat.llm.specs import answer_spec
from clinic_chat.timing import CaseTimer

RAW_LIST_DEFAULT_DEPTH = 1


async def run(deps: Deps, question: str, timer: CaseTimer, on_text: Callable[[str], None],
              provider: str = "sdk") -> dict:
    if provider in CLI_PROVIDERS:  # CLI 가 허브 MCP 를 직접 붙여 도구 루프를 돎(앱은 이벤트만 받음)
        res = await cli.run(answer_spec(deps.settings, provider), system=prompts.system_for("case1"), prompt=question,
                            timer=timer, on_text=on_text, mcp=McpAttach("hub", url=deps.settings.hub_raw_mcp_url,
                                                               tools=tuple(deps.startup.get("raw_tools") or ())),
                            collect_node_ids=False)
        res["trace"]["context"] = res["trace"]["tool_calls"]
        return res
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

        async def call(name: str, args: dict):
            if name == "raw_list" and "depth" not in args:  # 앱 기본 depth 1(21 파일 구조 전부가 커서) · 더 깊게는 직접 depth
                args = {**args, "depth": RAW_LIST_DEFAULT_DEPTH}
            return await s.call(name, args)

        res = await run_tool_loop(
            client=deps.anthropic, model=deps.settings.answer_model, system=prompts.system_for("case1"),
            question=question, tools=tools, call_tool=call, timer=timer, on_text=on_text,
            loop_max=deps.settings.case_tool_loop_max, on_tool_result=seen,
            tool_result_max_bytes=deps.settings.tool_result_max_bytes,
        )
    res["trace"]["context"] = res["trace"]["tool_calls"]
    res["trace"]["raw_manifest_sha16"] = meta.get("raw_manifest_sha16")
    return res
