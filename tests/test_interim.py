"""C 측정 보정 — 도구 호출 앞뒤 글 가르기(답 = 마지막 도구 뒤 글만 · 앞 글 = trace.interim_text[]) · 첫 답 글자 시간."""

from __future__ import annotations

import json

import httpx

from clinic_chat import progress
from clinic_chat.columns import col_a, col_c
from clinic_chat.columns.base import Deps
from clinic_chat.llm import cli
from clinic_chat.llm.cli import ClaudeIngest, CliSpec, new_result
from clinic_chat.runs import RunStore
from clinic_chat.timing import Clock, Timer
from clinic_chat.web.server import create_app
from tests.conftest import make_settings, stream
from tests.fake_hub import FakeHub
from tests.fake_jev import FakeJev
from tests.test_runs import setup as runs_setup
from tests.test_web import parse_sse

MEMO = "Two readings: concern and site. Query concern."
FC = json.dumps({"rows": [{"gold_table": "g_profile", "gid": "GPF-90001"}]})


class StepClock(Clock):
    """부를 때마다 10ms 씩 — 시간 순서를 시험에서 고정."""

    def __init__(self) -> None:
        self.n = 0

    def ms(self) -> float:
        self.n += 1
        return self.n * 10.0


def ingest(text: str) -> tuple[dict, Timer, list[dict]]:
    res, timer, evs = new_result(CliSpec("c", "m", "low", 1)), Timer(StepClock()), []
    ing = ClaudeIngest(res, timer, None)
    with progress.listen(evs.append):
        for ln in text.splitlines():
            ing.line(ln)
    timer.finish()
    res["timing"] = timer.as_dict()
    return res, timer, evs


def test_memo_then_tool_then_answer():
    res, _, evs = ingest(stream("가짜시술 · 가짜계열이 있어요.", tools=[("mcp__hub__facets", {}, FC)], memos=[MEMO]))
    assert res["answer"] == "가짜시술 · 가짜계열이 있어요." and res["interim_text"] == [MEMO]
    t = res["timing"]
    assert t["ttft_ms"] < t["first_answer_text_ms"]  # 첫 글자 = 메모 · 첫 답 글자 = 도구 뒤
    assert [e["event"] for e in evs] == ["delta", "interim", "delta"] and evs[1]["text"] == MEMO


def test_no_memo_and_no_tools_same_times():
    res, _, evs = ingest(stream("답", tools=[("mcp__hub__facets", {}, FC)], memos=[]))
    assert res["answer"] == "답" and res["interim_text"] == []
    assert res["timing"]["ttft_ms"] == res["timing"]["first_answer_text_ms"]
    assert "interim" not in [e["event"] for e in evs]
    res, _, _ = ingest(stream("B 답"))  # 도구 없음(B 계열 꼴)
    assert res["answer"] == "B 답" and res["timing"]["ttft_ms"] == res["timing"]["first_answer_text_ms"]


def test_many_tools_each_memo_split_once():
    tools = [("mcp__hub__resolve", {}, "{}"), ("mcp__hub__scope", {}, "{}"), ("mcp__hub__facets", {}, FC)]
    res, _, evs = ingest(stream("최종 답", tools=tools, memos=["m1", "", "m3"]))
    assert res["answer"] == "최종 답" and res["interim_text"] == ["m1", "m3"]
    assert [e.get("text") for e in evs if e["event"] == "interim"] == ["m1", "m3"]
    assert len(res["tool_calls"]) == 3


def test_memo_only_no_final_text_not_revived_from_result():
    """마지막 도구 뒤 글이 없으면 답은 비고 result 문자열이 메모와 같아도 답으로 되살리지 않는다."""
    lines = stream("", tools=[("mcp__hub__facets", {}, FC)], memos=[MEMO]).splitlines()
    last = json.loads(lines[-1])
    last["result"] = MEMO
    res, _, _ = ingest("\n".join([*lines[:-1], json.dumps(last)]))
    assert res["answer"] == "" and res["interim_text"] == [MEMO]


def test_assistant_only_tool_use_still_splits():
    """부분 메시지(content_block_start) 없이 assistant tool_use 만 와도 가른다."""
    res, _, _ = ingest(stream("답", tools=[("mcp__hub__facets", {}, FC)], memos=None)
                       .replace('"message_start", "message": {}}}\n', '"message_start", "message": {}}}\n'
                                '{"type": "stream_event", "event": {"type": "content_block_delta", "delta": '
                                '{"type": "text_delta", "text": "메모"}}}\n', 1))
    assert res["answer"] == "답" and res["interim_text"] == ["메모"]


def _deps(tmp_path, bin_, **kw):
    s = make_settings(tmp_path, claude_cli_bin=bin_, **kw)
    return s, Deps(s, FakeHub().client(), FakeJev().jev(s))


async def test_col_c_and_col_a_trace_interim(tmp_path, fake_cli):
    bin_, route, _ = fake_cli
    (route / "tools.jsonl").write_text(stream("답 [GPF-90001]", mcp="hub", tools=[("mcp__hub__facets", {}, FC)],
                                              memos=[MEMO]), encoding="utf-8")
    _, d = _deps(tmp_path, bin_)
    out = await col_c.run("q", d)
    assert out.result["answer"] == "답 [GPF-90001]" and MEMO not in out.result["answer"]
    assert out.result["trace"]["interim_text"] == [MEMO]
    t = out.result["timing"]
    assert t["first_answer_text_ms"] >= t["ttft_ms"]
    get = json.dumps({"path": "fake-alpha.items[0]", "truncated": False, "value": {}})
    (route / "tools.jsonl").write_text(stream("A 답", mcp="clinic-chat-raw", tools=[
        ("mcp__clinic-chat-raw__raw_get", {"path": "fake-alpha.items[0]"}, get)], memos=["원장 메모"]), encoding="utf-8")
    _, d = _deps(tmp_path, bin_, raw_dir=str(tmp_path))
    out = await col_a.run("q", d)
    assert out.result["answer"] == "A 답" and out.result["trace"]["interim_text"] == ["원장 메모"]


async def test_screen_click_to_first_answer(tmp_path, fake_cli):
    """화면 — C 는 첫 답 글자가 메모 첫 글자보다 늦다 · B 는 두 값 같다 · 표 첫 칸 = 첫 답 글자."""
    s, d, _ = runs_setup(tmp_path, fake_cli)
    _, route, _ = fake_cli
    (route / "tools.jsonl").write_text(stream("C 답", mcp="hub", tools=[("mcp__hub__facets", {}, FC)], memos=[MEMO]),
                                       encoding="utf-8")
    app = create_app(s, deps_factory=lambda _s: d)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/ask", json={"question": "가짜 질문", "columns": ["b", "c"], "b_on": False})
        rid = r.json()["run_id"]
        ev = parse_sse((await c.get(f"/api/runs/{rid}/events")).text)
        assert [x["text"] for k, x in ev if k == "interim" and x["column"] == "c"] == [MEMO]
        done = {x["column"]: x for k, x in ev if k == "done"}
        dc, db = done["c"], done["b"]
        assert dc["click_to_first_answer_ms"] >= dc["click_to_first_text_ms"]
        assert dc["speed"]["click_to_first_answer_ms"] == dc["click_to_first_answer_ms"]
        assert db["click_to_first_answer_ms"] == db["click_to_first_text_ms"]
        assert db["speed"]["first_answer_text_ms"] == db["speed"]["ttft_ms"]
        row = RunStore(tmp_path / "runs").get(rid)
        assert row["columns"]["c"]["answer"] == "C 답" and row["columns"]["c"]["trace"]["interim_text"] == [MEMO]
        t = (await c.post("/api/speed/table", json={"run_ids": [rid]})).json()
        assert t["stats"]["c"]["click_to_first_answer_ms"]["n"] == 1


async def test_cli_run_answer_excludes_memo(tmp_path, fake_cli):
    bin_, route, _ = fake_cli
    (route / "answer.jsonl").write_text(stream("진짜 답", tools=[("mcp__x__y", {}, "{}")], memos=[MEMO]),
                                        encoding="utf-8")
    r = await cli.run(CliSpec(bin_, "m", "low", 20), system="S", prompt="P")
    assert r["answer"] == "진짜 답" and r["interim_text"] == [MEMO]
