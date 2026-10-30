"""② Opus 채점(SPEC-006 §5-2) — 가리기(seed 로 섞어 A · B · C) · 원장 도구 3(앱 안 로컬 · R-10) · 항목 5 × 답 3.

effort medium · thinking 기본 · tool_choice auto · 도구 루프(상한 CASE_TOOL_LOOP_MAX) 뒤 마지막 응답을 구조화 출력으로.
실패 근거 `{file, path, quote}` 는 원장 로컬 파일로 확인 → quote 가 그 경로 원문에 없으면 `invalid`.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

from clinic_chat import prompts
from clinic_chat.clients.anthropic_client import CACHE, block_to_param, cached_system, usage_dict
from clinic_chat.deps import NO_KEY, Deps
from clinic_chat.pricing import cost_usd, sum_usage
from clinic_chat.quality.raw_tools import RAW_TOOL_DEFS, RawToolError
from clinic_chat.quality.word_check import final_text

ITEMS = ["accuracy", "safety", "candidates", "uncertainty", "on_topic"]
VERDICTS = ["통과", "실패", "해당 없음"]
LABELS = ["A", "B", "C"]
JUDGE_EFFORT = "medium"
JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answers"],
    "properties": {"answers": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["label", "items"],
        "properties": {
            "label": {"type": "string", "enum": LABELS},
            "items": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["item", "verdict", "claim", "evidence"],
                "properties": {
                    "item": {"type": "string", "enum": ITEMS},
                    "verdict": {"type": "string", "enum": VERDICTS},
                    "claim": {"type": "string"},
                    "evidence": {"anyOf": [{"type": "null"}, {
                        "type": "object", "additionalProperties": False, "required": ["file", "path", "quote"],
                        "properties": {"file": {"type": "string"}, "path": {"type": "string"},
                                       "quote": {"type": "string"}}}]}}}}}}}},
}


def blind_order(cases: list[str], seed: int) -> list[str]:
    order = list(cases)
    random.Random(seed).shuffle(order)
    return order


def judge_input(question: str, answers: dict[str, str], order: list[str]) -> str:
    """가린 입력 — 답 원문만(case 이름 · 도구 흔적 · 속도 없음)."""
    parts = [f"질문: {question}", ""]
    for label, c in zip(LABELS, order):
        parts += [f"--- 답 {label} ---", answers[c], ""]
    return "\n".join(parts)


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


async def _tool(store: Any, name: str, args: dict) -> tuple[str, bool]:
    try:
        return json.dumps(store.call(name, args), ensure_ascii=False), False
    except (RawToolError, TypeError) as e:
        return f"오류: {e}", True


async def judge(deps: Deps, question: str, answers: dict[str, str], order: list[str]) -> dict:
    if deps.anthropic is None:
        raise RuntimeError(NO_KEY)
    if deps.raw_store is None:
        raise RuntimeError("RAW_DIR 필요")
    s = deps.settings
    system = prompts.load("judge")
    messages: list[dict] = [{"role": "user", "content": judge_input(question, answers, order)}]
    # 캐시: 시스템(+도구) 표시 · 루프는 마지막 블록 자동 표시(바퀴마다 앞 대화를 캐시 읽기로)
    base = {"model": s.judge_model, "max_tokens": 16000, "system": cached_system(system), "tools": RAW_TOOL_DEFS,
            "cache_control": CACHE}
    usages: list[dict] = []
    tool_calls = 0
    rounds = 0
    while rounds < s.case_tool_loop_max:
        rounds += 1
        msg = await deps.anthropic.messages.create(**base, messages=messages, tool_choice={"type": "auto"},
                                                   output_config={"effort": JUDGE_EFFORT})
        usages.append(usage_dict(msg.usage))
        uses = [b for b in msg.content if b.type == "tool_use"]
        if msg.stop_reason != "tool_use" or not uses:
            messages.append({"role": "assistant", "content": [block_to_param(b) for b in msg.content]})
            break
        messages.append({"role": "assistant", "content": [block_to_param(b) for b in msg.content]})
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

    label_case = dict(zip(LABELS, order))
    per_case: dict[str, dict] = {}
    for a in data.get("answers", []):
        c = label_case.get(a["label"])
        if c is None:
            continue
        items, failures = {}, []
        for it in a["items"]:
            items[it["item"]] = it["verdict"]
            if it["verdict"] == "실패":
                valid = check_evidence(deps.raw_store, it.get("evidence"))
                failures.append({"item": it["item"], "answer": a["label"], "claim": it["claim"],
                                 "evidence": it.get("evidence"), "status": "valid" if valid else "invalid"})
        per_case[c] = {"label": a["label"], "items": items, "failures": failures,
                       "pass_count": sum(1 for v in items.values() if v == "통과"),
                       "invalid_count": sum(1 for f in failures if f["status"] == "invalid")}
    usage = sum_usage(usages)
    return {"per_case": per_case, "rounds": rounds, "tool_calls": tool_calls, "judge_model": s.judge_model,
            "usage": usage, "cost_usd": round(sum(cost_usd(s.judge_model, u) or 0 for u in usages), 6),
            "calls": len(usages)}
