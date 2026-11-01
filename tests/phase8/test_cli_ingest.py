"""WORK-006 기록 칸(SPEC-006 v2 §3-5 · §3-6 · §4-2) — `delivered_bytes` · `truncated`(마커) · `intent_passed` · `stray`
· 판 단위 `intent_ratio` · `intent_values` · `stray_tools`. 모의 CLI 만(실물 호출 없음)."""

from __future__ import annotations

import json

from clinic_chat.cases.loop import VALID_INTENTS, cap_text, mark_call, mark_result, summarize_calls
from clinic_chat.config import TRUNCATION_MARKERS, truncation_markers_sha
from clinic_chat.llm import cli
from clinic_chat.llm.cli import McpAttach
from tests.cli.test_cli import RESULT, TOOL_RESULT, fake, init, j, spec, timer, turn  # noqa: F401 — fake 는 fixture

HUB = McpAttach("hub", url="http://hub.test/procedure-hub/gold-mcp/mcp",
                intent_tools=frozenset({"recommend", "procedure_card", "check_conditions"}))
PREVIEW = "Output too large (92.5KB). Full output saved to: /tmp/x/tool-results/mcp-hub-blob.txt\n\nPreview (first 2KB):\n{"


def tu(i, name, inp):
    return {"type": "tool_use", "id": i, "name": name, "input": inp}


def tr(i, text, err=False):
    return {"type": "tool_result", "tool_use_id": i, "content": [{"type": "text", "text": text}], "is_error": err}


CLAUDE = j(init("hub"), *turn(["찾아볼게요. "], stop="tool_use"),
           {"type": "assistant", "message": {"content": [
               tu("a", "mcp__hub__procedure_card", {"id": "unit_procedure:U5-140", "intent": "I-02"}),
               tu("b", "mcp__hub__check_conditions", {"id": "unit_procedure:U5-140", "intent": "none"}),
               tu("c", "mcp__hub__resolve", {"text": "소프웨이브"}),
               tu("d", "mcp__hub__recommend", {"concerns": ["concern:P2"]}),
               tu("e", "Bash", {"command": "jq . /tmp/x/tool-results/mcp-hub-blob.txt"})]}},
           {"type": "user", "message": {"content": [
               tr("a", TOOL_RESULT), tr("b", PREVIEW), tr("c", TOOL_RESULT), tr("d", TOOL_RESULT),
               tr("e", "Error: No such tool available: Bash", err=True)]}},
           *turn(["답"]), {**RESULT, "num_turns": 2})


async def test_claude_fields_and_summary(fake):  # noqa: F811
    lines, _ = fake
    lines(CLAUDE)
    r = await cli.run(spec("claude"), system="s", prompt="q", timer=timer(), on_text=lambda t: None, mcp=HUB,
                      collect_node_ids=True)
    assert r["error"] is None
    c = {x["tool"]: x for x in r["trace"]["tool_calls"]}
    card, chk, res, rec, bash = c["procedure_card"], c["check_conditions"], c["resolve"], c["recommend"], c["Bash"]
    assert card["delivered_bytes"] == card["output_bytes"] == len(TOOL_RESULT.encode()) and card["truncated"] is False
    assert chk["truncated"] is True and chk["delivered_bytes"] == len(PREVIEW.encode())
    assert card["intent_passed"] is True and chk["intent_passed"] is False  # 「none」 = 틀린 값
    assert rec["intent_passed"] is False  # 스키마에 intent 있는데 안 넘김
    assert res["intent_passed"] is None  # 스키마에 intent 없음
    assert bash["stray"] is True and bash["intent_passed"] is None and card["stray"] is False
    t = r["trace"]
    assert t["intent_ratio"] == round(1 / 3, 4) and t["intent_values"] == {"I-02": 1, "none": 1}
    assert t["stray_tools"] == 1


CODEX = j({"type": "thread.started", "thread_id": "th1"}, {"type": "turn.started"},
          {"type": "item.completed", "item": {"id": "i1", "type": "mcp_tool_call", "server": "hub", "tool": "check_conditions",
                                              "arguments": {"id": "unit_procedure:U5-140", "intent": "I-09"},
                                              "status": "completed", "error": None,
                                              "result": {"content": [{"type": "text", "text": TOOL_RESULT[:40]
                                                                      + "…<truncated omitted_approx_tokens=812>"}]}}},
          {"type": "item.completed", "item": {"id": "i2", "type": "command_execution", "command": "ls",
                                              "status": "failed"}},
          {"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": "답"}},
          {"type": "turn.completed", "usage": {"input_tokens": 9, "output_tokens": 4}})


async def test_codex_fields_and_summary(fake):  # noqa: F811
    lines, _ = fake
    lines(CODEX)
    r = await cli.run(spec("codex"), system="s", prompt="q", timer=timer(), on_text=lambda t: None, mcp=HUB,
                      collect_node_ids=True)
    a, b = r["trace"]["tool_calls"]
    assert a["truncated"] is True and a["intent_passed"] is True and a["stray"] is False
    assert b["stray"] is True and b["intent_passed"] is None
    assert r["trace"]["intent_ratio"] == 1.0 and r["trace"]["stray_tools"] == 1


def test_sdk_marks_and_no_cap():
    big = "가" * 30_000
    text, cut = cap_text(big, 0)
    assert text == big and cut is False  # 0 = 상한 없음
    call = mark_result(mark_call({"tool": "compare", "input": {"procedures": [], "intent": "I-06"}},
                                 intent_tools={"compare"}, stray=False), text, "sdk")
    assert call["delivered_bytes"] == len(big.encode()) and call["truncated"] is False and call["intent_passed"] is True
    cut_text, cut = cap_text(big, 100)
    assert cut and mark_result({"tool": "x"}, cut_text, "sdk")["truncated"] is True  # 앱 표시도 마커로 잡힘
    t = summarize_calls({"tool_calls": [call, mark_call({"tool": "raw_get", "input": {}}, intent_tools=(), stray=False)]})
    assert t["intent_ratio"] == 1.0 and t["intent_values"] == {"I-06": 1} and t["stray_tools"] == 0


def test_markers_constant():
    assert VALID_INTENTS == {f"I-{i:02d}" for i in range(1, 13)}
    assert len(TRUNCATION_MARKERS["claude-cli"]) == 7 and len(TRUNCATION_MARKERS["codex-cli"]) == 5
    assert len(truncation_markers_sha()) == 16
    assert json.dumps(TRUNCATION_MARKERS, ensure_ascii=False)  # 기록 가능한 꼴
