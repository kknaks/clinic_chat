"""② Opus 채점 — **답 하나씩 같은 기준으로 절대 채점**(10-30 사용자: 열마다 따로 · 끝나는 대로).

SPEC-006 §5-2 는 세 답을 섞어 A · B · C 로 한 번에 비교했다 → 지금은 열 답 하나를 받아 항목 5 를 매긴다(SPEC 다른 점).
가리기는 유지: 채점 입력에 case 이름 · provider · 도구 흔적 · 속도 · 다른 답이 없다(질문 + 답 원문만).
원장 도구 3(앱 안 로컬 · R-10 · CLI 판은 로컬 stdio MCP) · 실패 근거 `{file, path, quote}` 를 원장 로컬 파일로 확인 → 없으면 `invalid`.
sdk 판: effort medium · 도구 루프(상한 CASE_TOOL_LOOP_MAX) 뒤 구조화 출력 / CLI 판: Claude CLI Opus · --json-schema.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from clinic_chat import prompts
from clinic_chat.clients.anthropic_client import CACHE, block_to_param, cached_system, usage_dict
from clinic_chat.deps import NO_KEY, Deps
from clinic_chat.pricing import cost_usd, sum_usage
from clinic_chat.quality.raw_tools import RAW_TOOL_DEFS, RawToolError
from clinic_chat.quality.word_check import final_text

ITEMS = ["accuracy", "safety", "candidates", "uncertainty", "on_topic"]
VERDICTS = ["통과", "실패", "해당 없음"]
JUDGE_EFFORT = "medium"
MODE = "absolute"
ITEM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["item", "verdict", "claim", "evidence"],
    "properties": {
        "item": {"type": "string", "enum": ITEMS},
        "verdict": {"type": "string", "enum": VERDICTS},
        "claim": {"type": "string"},
        "evidence": {"anyOf": [{"type": "null"}, {
            "type": "object", "additionalProperties": False, "required": ["file", "path", "quote"],
            "properties": {"file": {"type": "string"}, "path": {"type": "string"}, "quote": {"type": "string"}}}]}}}
JUDGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
                "properties": {"items": {"type": "array", "items": ITEM_SCHEMA}}}


def judge_input(question: str, answer: str) -> str:
    """가린 입력 — 질문 + 답 원문만(case 이름 · provider · 도구 흔적 · 속도 · 다른 답 없음)."""
    return f"질문: {question}\n\n--- 답 ---\n{answer}\n"


def _strings(v: Any):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, str):
        yield v


def check_evidence(store: Any, ev: dict | None) -> bool:
    if not ev or not ev.get("quote") or not ev.get("path"):
        return False
    path = ev["path"]
    file = ev.get("file") or ""
    if file and not (path == file or path.startswith((file + ".", file + "["))):
        path = file + ("" if path.startswith("[") else ".") + path
    try:
        v = store.resolve(path)
    except RawToolError:
        return False
    return any(ev["quote"] in s for s in _strings(v))


def verdicts(data: dict, store: Any) -> dict:
    """구조화 출력 {items:[…]} → {items: {항목: 판정}, failures, pass_count, invalid_count}."""
    items, failures = {}, []
    for it in data.get("items", []):
        items[it["item"]] = it["verdict"]
        if it["verdict"] == "실패":
            valid = check_evidence(store, it.get("evidence"))
            failures.append({"item": it["item"], "claim": it["claim"], "evidence": it.get("evidence"),
                             "status": "valid" if valid else "invalid"})
    return {"items": items, "failures": failures, "pass_count": sum(1 for v in items.values() if v == "통과"),
            "invalid_count": sum(1 for f in failures if f["status"] == "invalid")}


async def _tool(store: Any, name: str, args: dict) -> tuple[str, bool]:
    try:
        return json.dumps(store.call(name, args), ensure_ascii=False), False
    except (RawToolError, TypeError) as e:
        return f"오류: {e}", True


async def judge_one_cli(deps: Deps, question: str, answer: str) -> dict:
    """Claude CLI Opus · 원장 도구 3 = 로컬 stdio MCP(raw_mcp) · --json-schema 로 마지막 답."""
    import sys

    from clinic_chat.config import ROOT
    from clinic_chat.llm import cli
    from clinic_chat.llm.cli import McpAttach
    from clinic_chat.llm.specs import judge_spec
    from clinic_chat.timing import CaseTimer, Clock

    s = deps.settings
    raw = McpAttach("raw", command=sys.executable, args=["-m", "clinic_chat.quality.raw_mcp"],
                    env={"RAW_DIR": s.raw_dir, "PYTHONPATH": str(ROOT)})
    prompt = judge_input(question, answer) + "\n자료 확인을 마치면 채점 결과를 지정된 JSON 꼴로만 내라."
    c = Clock(now=deps.now)
    c.start()
    r = await cli.run(judge_spec(s, "judge"), system=prompts.load("judge"), prompt=prompt, timer=CaseTimer(c),
                      on_text=lambda t: None, mcp=raw, schema=JUDGE_SCHEMA)
    if r["error"] or not isinstance(r.get("structured"), dict):
        raise RuntimeError(r["error"] or "채점 구조화 출력 없음")
    return {**verdicts(r["structured"], deps.raw_store), "mode": MODE, "provider": "claude-cli",
            "judge_model": s.judge_model, "rounds": r["timing"].get("llm_round_trips"),
            "tool_calls": len(r["trace"]["tool_calls"]), "usage": (r["tokens"] or {}).get("total", {}),
            "cost_usd": r["cost"].get("usd_estimate"), "billed": "subscription", "calls": 1,
            "ms": r["timing"].get("total_ms")}


async def judge_one(deps: Deps, question: str, answer: str) -> dict:
    """열 답 하나 채점(절대) — 결과 꼴은 sdk · CLI 같음."""
    if deps.raw_store is None:
        raise RuntimeError("RAW_DIR 필요")
    if deps.settings.judge_provider == "claude-cli":
        return await judge_one_cli(deps, question, answer)
    if deps.anthropic is None:
        raise RuntimeError(NO_KEY)
    s = deps.settings
    messages: list[dict] = [{"role": "user", "content": judge_input(question, answer)}]
    # 캐시: 시스템(+도구) 표시 · 루프는 마지막 블록 자동 표시(바퀴마다 앞 대화를 캐시 읽기로)
    base = {"model": s.judge_model, "max_tokens": 16000, "system": cached_system(prompts.load("judge")),
            "tools": RAW_TOOL_DEFS, "cache_control": CACHE}
    usages: list[dict] = []
    tool_calls = rounds = 0
    while rounds < s.case_tool_loop_max:
        rounds += 1
        msg = await deps.anthropic.messages.create(**base, messages=messages, tool_choice={"type": "auto"},
                                                   output_config={"effort": JUDGE_EFFORT})
        usages.append(usage_dict(msg.usage))
        uses = [b for b in msg.content if b.type == "tool_use"]
        messages.append({"role": "assistant", "content": [block_to_param(b) for b in msg.content]})
        if msg.stop_reason != "tool_use" or not uses:
            break
        outs = await asyncio.gather(*(_tool(deps.raw_store, b.name, dict(b.input or {})) for b in uses))
        tool_calls += len(uses)
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": b.id, "content": t, "is_error": err} for b, (t, err) in zip(uses, outs)]})
    ask = {"type": "text", "text": "자료 확인을 마쳤으면 채점 결과를 지정된 JSON 꼴로만 내라."}
    if messages[-1]["role"] == "user":  # 상한에 닿아 도구 결과로 끝났으면 같은 user 메시지에 붙임
        messages[-1]["content"].append(ask)
    else:
        messages.append({"role": "user", "content": [ask]})
    final = await deps.anthropic.messages.create(
        **base, messages=messages, tool_choice={"type": "none"},
        output_config={"effort": JUDGE_EFFORT, "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}})
    usages.append(usage_dict(final.usage))
    data = json.loads(final_text(final))
    return {**verdicts(data, deps.raw_store), "mode": MODE, "provider": "sdk", "judge_model": s.judge_model,
            "rounds": rounds, "tool_calls": tool_calls, "usage": sum_usage(usages),
            "cost_usd": round(sum(cost_usd(s.judge_model, u) or 0 for u in usages), 6), "calls": len(usages)}
