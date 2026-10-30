"""Phase 2 — case1 · case2 도구 루프(모의 Anthropic 스트림 + 모의 허브 MCP)."""

from __future__ import annotations

from clinic_chat.cases import case1, case2
from clinic_chat.deps import NO_KEY
from clinic_chat.timing import CaseTimer, Clock
from tests.fake_anthropic import FakeAnthropic, Turn


def timer() -> CaseTimer:
    c = Clock()
    c.start()
    return CaseTimer(c)


async def run1(deps, q="이마 주름"):
    out: list[str] = []
    r = await case1.run(deps, q, timer(), out.append)
    return r, "".join(out)


async def run2(deps, q="이마 주름"):
    out: list[str] = []
    r = await case2.run(deps, q, timer(), out.append)
    return r, "".join(out)


def assert_same_conditions(calls):
    for p in calls:
        assert p["thinking"] == {"type": "between_tools"}  # 다른 칸 없음
        assert p["output_config"] == {"effort": "high"}
        assert p["model"] == "claude-sonnet-5-5"
        assert p.get("tool_choice", {"type": "auto"})["type"] in ("auto", "none")  # 강제값(any · tool) 안 씀


async def test_no_tool(deps_factory):
    fa = FakeAnthropic([Turn(text=["안녕", "하세요"])])
    r, streamed = await run1(deps_factory(anthropic=fa))
    assert r["answer"] == streamed == "안녕하세요"
    assert r["timing"]["llm_round_trips"] == 1 and r["trace"]["tool_calls"] == []
    assert r["stop_reason"] == "end_turn" and not r["loop_capped"]
    assert [t["name"] for t in fa.calls[0]["tools"]] == ["raw_list", "raw_get", "raw_search"]
    assert_same_conditions(fa.calls)


async def test_one_tool_case1(deps_factory, hub):
    fa = FakeAnthropic([Turn(tools=[("raw_search", {"query": "보톡스"})]), Turn(text=["답"])])
    r, _ = await run1(deps_factory(anthropic=fa))
    assert [c["tool"] for c in r["trace"]["tool_calls"]] == ["raw_search"]
    assert r["trace"]["context"] == r["trace"]["tool_calls"]
    assert r["trace"]["raw_manifest_sha16"] == hub.store.dir_manifest_sha16
    assert [s["name"] for s in r["timing"]["stages"]] == ["claude#1", "tool:raw_search#1", "claude#2"]
    tr = fa.calls[1]["messages"][-1]["content"]
    assert tr[0]["type"] == "tool_result" and not tr[0]["is_error"]
    assert_same_conditions(fa.calls)


async def test_many_rounds_case2_node_ids(deps_factory):
    fa = FakeAnthropic([
        Turn(tools=[("resolve", {"text": "이마 주름"})]),
        Turn(tools=[("recommend", {"concerns": ["concern:P2"], "site": "site:이마"})]),
        Turn(text=["끝"]),
    ])
    r, _ = await run2(deps_factory(anthropic=fa))
    calls = r["trace"]["tool_calls"]
    assert [c["tool"] for c in calls] == ["resolve", "recommend"]
    assert "unit_procedure:U5-001" in calls[1]["node_ids"] and "concern:P2" in calls[1]["node_ids"]
    assert all(c["output_bytes"] > 0 for c in calls)
    assert r["trace"]["gold_build_hash"] == "fixture0build0h1"
    assert r["timing"]["llm_round_trips"] == 3
    assert_same_conditions(fa.calls)


async def test_parallel_tools_one_message(deps_factory):
    fa = FakeAnthropic([
        Turn(text=["찾아볼게요. "], tools=[("procedure_card", {"id": "unit_procedure:U5-140"}),
                                         ("procedure_card", {"id": "unit_procedure:U5-102"})]),
        Turn(text=["비교 답"]),
    ])
    r, _ = await run2(deps_factory(anthropic=fa))
    last_user = fa.calls[1]["messages"][-1]
    assert last_user["role"] == "user" and [b["type"] for b in last_user["content"]] == ["tool_result"] * 2
    assert fa.calls[1]["messages"][-2]["role"] == "assistant"
    names = [s["name"] for s in r["timing"]["stages"]]
    assert "tool:procedure_card#1" in names and "tool:procedure_card#2" in names
    assert r["answer"] == "찾아볼게요. 비교 답"
    assert r["timing"]["ttft_ms"] <= r["timing"]["ttft_answer_ms"]


async def test_loop_cap(deps_factory):
    fa = FakeAnthropic(lambda p, n: Turn(text=["마지막"]) if p.get("tool_choice", {}).get("type") == "none"
                       else Turn(tools=[("vocab", {})]))
    r, _ = await run2(deps_factory(anthropic=fa, case_tool_loop_max=3))
    assert len(fa.calls) == 4
    assert [p["tool_choice"]["type"] for p in fa.calls] == ["auto", "auto", "auto", "none"]
    assert r["loop_capped"] is True and r["answer"] == "마지막"
    assert_same_conditions(fa.calls)


async def test_refusal(deps_factory):
    fa = FakeAnthropic([Turn(stop_reason="refusal")])
    r, _ = await run2(deps_factory(anthropic=fa))
    assert r["refusal"] is True and r["stop_reason"] == "refusal" and r["error"] is None


async def test_mcp_error_is_error_result(deps_factory):
    fa = FakeAnthropic([Turn(tools=[("recommend", {"concerns": ["concern:없음"]})]), Turn(text=["되묻기"])])
    r, _ = await run2(deps_factory(anthropic=fa))
    assert r["trace"]["tool_calls"][0]["is_error"] is True
    tr = fa.calls[1]["messages"][-1]["content"][0]
    assert tr["is_error"] is True and "없는 id" in tr["content"]
    assert r["answer"] == "되묻기"


async def test_thinking_block_passed_back_and_not_ttft(deps_factory):
    fa = FakeAnthropic([Turn(thinking="", tools=[("vocab", {})]), Turn(text=["답"])])
    r, _ = await run2(deps_factory(anthropic=fa))
    assistant = fa.calls[1]["messages"][1]
    assert assistant["content"][0]["type"] == "thinking"
    assert r["answer"] == "답"


async def test_no_key(deps_factory):
    r, _ = await run1(deps_factory(anthropic=None))
    assert r["error"] == NO_KEY
    r, _ = await run2(deps_factory(anthropic=None))
    assert r["error"] == NO_KEY
