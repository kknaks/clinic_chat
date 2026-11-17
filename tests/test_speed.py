"""WORK-004 · WORK-006 WP-6 — 속도 탭: 열 = b · c(+ 고르면 b_full) · 속도 값 · 화면 API(모의만 — 실호출 0).

0.5.0 부터 「B 선별」(`b`)은 없다 — 새 B 가 단계 ① 에서 종류를 골라 읽는다(O-26 ②).
"""

from __future__ import annotations

import json

import httpx

from clinic_chat import speed
from clinic_chat.runner import ALL_COLS, COLS, run_question
from clinic_chat.runs import RunStore
from clinic_chat.web.server import create_app, done_payload
from tests.test_runs import setup as runs_setup
from tests.test_web import parse_sse


def test_columns_names():
    assert COLS == ("a", "b", "c") and ALL_COLS == ("a", "b", "c", "b_full")


async def test_b_full_column_over_fakes(tmp_path, fake_cli):
    """b_full — 단계 ① 은 B 와 같고 읽는 종류만 그 대상의 표에 있는 것 전부(B-2) · 채점 대상 아님 · 입력이 B 보다 크다."""
    from clinic_chat.columns.base import Deps
    from tests.fake_jev import FakeJev
    from tests.walk_fixture import P1, cand, jev_noul, make_hub

    s, _, log = runs_setup(tmp_path, fake_cli)
    d = Deps(s, make_hub(resolve=[cand(P1)]).client(), FakeJev(noul_fn=jev_noul()).jev(s))
    store = RunStore(tmp_path / "runs")
    r = await run_question("가짜 질문", d, columns=("b", "b_full"), b_on=False, store=store, seed=3)
    assert set(r["columns"]) == {"a", "b", "b_on", "c", "b_full"}
    b, bf = r["columns"]["b"], r["columns"]["b_full"]
    assert not bf["error"], bf["error"]
    assert set(bf["timing"]["parts"]) == {"jev_ms", "hub_ms", "llm_ms"}
    kb = {k["code"] for k in b["trace"]["front"]["kinds"] if k["decision"] == "read"}
    read_full = {x for rd in bf["trace"]["breadth"] for x in rd["kind"]}
    assert kb < read_full and bf["trace"]["llm_input_bytes"] > b["trace"]["llm_input_bytes"]
    assert [k["code"] for k in b["trace"]["front"]["kinds"]] == [k["code"] for k in bf["trace"]["front"]["kinds"]]


def test_speed_card_and_table():
    bb = {"end_state": "근거 충족", "timing": {"total_ms": 100.0, "ttft_ms": 60.0,
                                               "parts": {"jev_ms": 10, "hub_ms": 20, "llm_ms": 60}},
              "tokens": {"llm": [{"input_tokens": 3, "cache_read_input_tokens": 7, "output_tokens": 4}]},
              "trace": {"llm_input_bytes": 2048, "llm_pages": [{"thinking_seen": False}],
                        "front": {"kinds": [{"code": "overview", "noul": 0.9, "decision": "read"}]}}, "error": None}
    c = {"timing": {"total_ms": 200.0, "ttft_ms": 150.0, "stages": [
        {"name": "llm#1", "start_ms": 0, "end_ms": 50}, {"name": "tool:facets#1", "start_ms": 50, "end_ms": 80},
        {"name": "llm#2", "start_ms": 80, "end_ms": 200}]},
         "tokens": {"input_tokens": 10, "output_tokens": 20},
         "trace": {"tool_calls": [{"output_bytes": 1000}], "thinking_seen": True}, "error": None}
    k = speed.card("b", bb, model="m", effort="low")
    assert (k["total_ms"], k["ttft_ms"], k["input_kb"], k["input_tokens"], k["output_tokens"]) == (100.0, 60.0, 2.0, 10, 4)
    assert k["thinking"] is False and k["front_kinds"][0]["code"] == "overview" and "select_ms" not in k["parts"]
    kc = speed.card("c", c, question="ab")
    assert kc["parts"] == {"jev_ms": 0.0, "hub_ms": 30.0, "llm_ms": 170.0}
    assert kc["input_bytes"] == 1002 and kc["thinking"] is True and kc["front_kinds"] is None
    assert speed.card("c", None) is None
    # 새 꼴(코디 발견 — C 토큰 0): `tokens.llm[]` 왕복 합 · `trace.llm_input_bytes`
    c2 = {**c, "tokens": {"llm": [{"input_tokens": 2, "cache_read_input_tokens": 8, "output_tokens": 5},
                                  {"input_tokens": 3, "cache_creation_input_tokens": 7, "output_tokens": 6}]},
          "trace": {**c["trace"], "llm_input_bytes": 3072}}
    k2 = speed.card("c", c2, question="ab")
    assert (k2["input_tokens"], k2["output_tokens"], k2["input_kb"]) == (20, 11, 3.0)
    rows = [{"run_id": f"r{i}", "set_id": f"X0{i}", "question": "ab", "settings": {"answer_reasoning": "lowest_effort",
             "lowest_effort": "low", "effort": "medium"}, "models": {"answer": "m"},
             "columns": {"b": {**bb, "timing": {**bb["timing"], "total_ms": 100.0 * (i + 1)}}, "c": c}}
            for i in range(3)]
    t = speed.table(rows)
    assert t["columns"] == ["b", "c"] and len(t["rows"]) == 3
    assert t["stats"]["b"]["total_ms"] == {"n": 3, "mean": 200.0, "median": 200.0}
    assert t["stats"]["c"]["output_tokens"]["median"] == 20
    assert speed.row_cards(rows[0])["b"]["effort"] == "low" and speed.row_cards(rows[0])["c"]["effort"] == "medium"


def test_done_payload_has_speed():
    d = done_payload("c", {"timing": {"total_ms": 5.0, "stages": []}, "tokens": {}, "trace": {"tool_calls": []}},
                     "m", "medium", "q")
    assert d["speed"]["total_ms"] == 5.0 and d["speed"]["model"] == "m"
    assert done_payload("b_on", None, None, None)["speed"] is None


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_speed_api_over_real_runner(tmp_path, fake_cli):
    s, d, _ = runs_setup(tmp_path, fake_cli)
    app = create_app(s, deps_factory=lambda _s: d)
    async with client(app) as c:
        assert (await c.post("/api/ask", json={"question": "q", "columns": ["b_slim"]})).status_code == 400
        rids = []
        for _ in range(2):
            r = await c.post("/api/ask", json={"question": "가짜 질문", "columns": ["b", "c"], "b_on": False})
            assert r.status_code == 200 and r.json()["columns"] == ["b", "c"]
            rid = r.json()["run_id"]
            ev = parse_sse((await c.get(f"/api/runs/{rid}/events")).text)
            done = {x["column"]: x for k, x in ev if k == "done"}
            assert set(done) == {"b", "c"}
            assert done["b"]["speed"]["front_kinds"] and done["c"]["speed"]["parts"]
            assert any(k == "step" and x["column"] == "b" and x["step"] == "①" for k, x in ev)
            assert "".join(x["text"] for k, x in ev if k == "delta" and x["column"] == "b")
            rids.append(rid)
        one = (await c.get(f"/api/speed/{rids[0]}")).json()
        assert set(one["cards"]) == {"b", "c"}
        assert (await c.get("/api/speed/nope")).status_code == 404
        t = (await c.post("/api/speed/table", json={"run_ids": [*rids, "nope"]})).json()
        assert t["columns"] == ["b", "c"] and len(t["rows"]) == 2
        assert t["stats"]["b"]["total_ms"]["n"] == 2
        assert (await c.get("/static/speed.js")).status_code == 200
        html = (await c.get("/")).text
        assert "speed.js" in html and "tab-speed" in html


def _answer_efforts(log) -> list[tuple[bool, str]]:
    """모의 CLI 기록 → (MCP 붙음 여부, `--effort` 값) — 단계 ⑤ · C 답 호출만(Jev 는 CLI 아님)."""
    rows = [json.loads(x) for x in open(log, encoding="utf-8").read().splitlines() if x.strip()]
    return [("--mcp-config" in r["argv"], r["argv"][r["argv"].index("--effort") + 1])
            for r in rows if "--system-prompt" in r["argv"]]


async def test_effort_override_same_for_every_column(tmp_path, fake_cli):
    """속도 탭 effort — 고른 값이 B · C · B 전체 세 열에 똑같이 가고 기록 `efforts` 에 남는다."""
    s, d, log = runs_setup(tmp_path, fake_cli)
    store = RunStore(tmp_path / "runs")
    r = await run_question("가짜 질문", d, columns=("b", "c", "b_full"), b_on=False, store=store, seed=5,
                           effort="high")
    got = _answer_efforts(log)
    assert any(m for m, _ in got) and any(not m for m, _ in got)  # C(MCP) · B 계열 둘 다 불렸다
    assert {e for _, e in got} == {"high"}
    assert r["efforts"] == {"b": "high", "c": "high", "b_full": "high"} and r["effort_override"] == "high"
    cards = speed.row_cards(store.get(r["run_id"]))
    assert {c["effort"] for c in cards.values()} == {"high"}


async def test_effort_default_unchanged(tmp_path, fake_cli):
    """effort 를 안 주면 그대로 — B 계열 = 가장 낮은 값 · C = batch effort(정확도 탭 · CLI · batch)."""
    s, d, log = runs_setup(tmp_path, fake_cli)
    r = await run_question("가짜 질문", d, columns=("b", "c"), b_on=False, store=RunStore(tmp_path / "runs"), seed=5)
    got = _answer_efforts(log)
    assert {e for m, e in got if m} == {s.claude_cli_effort} and {e for m, e in got if not m} == {s.lowest_effort}
    assert r["efforts"] == {"b": s.lowest_effort, "c": s.claude_cli_effort} and r["effort_override"] is None


async def test_speed_api_effort(tmp_path, fake_cli):
    s, d, log = runs_setup(tmp_path, fake_cli)
    app = create_app(s, deps_factory=lambda _s: d)
    async with client(app) as c:
        assert (await c.post("/api/ask", json={"question": "q", "columns": ["c"], "effort": "max"})).status_code == 422
        r = await c.post("/api/ask", json={"question": "가짜 질문", "columns": ["b", "c"], "b_on": False,
                                           "effort": "medium"})
        assert r.status_code == 200 and r.json()["effort"] == "medium"
        rid = r.json()["run_id"]
        ev = parse_sse((await c.get(f"/api/runs/{rid}/events")).text)
        done = {x["column"]: x for k, x in ev if k == "done"}
        assert {x["speed"]["effort"] for x in done.values()} == {"medium"}
        assert {e for _, e in _answer_efforts(log)} == {"medium"}
        t = (await c.post("/api/speed/table", json={"run_ids": [rid], "effort": "medium"})).json()
        assert t["effort"] == "medium" and t["effort_mismatch"] == []
        t = (await c.post("/api/speed/table", json={"run_ids": [rid], "effort": "low"})).json()
        assert t["effort_mismatch"] == [rid]
        html = (await c.get("/")).text
        assert 'id="s-effort"' in html and "B 전체" in html


def _start_done(events: list[tuple[str, dict]]) -> list[tuple[str, str]]:
    return [(k, x["column"]) for k, x in events if k in ("start", "done") and x.get("column") in ("b", "c")]


async def test_screen_runs_columns_concurrently_one_row(tmp_path, fake_cli):
    """화면 = 고른 열 동시 시작 · 같은 run_id · 기록 한 줄 · 열마다 누른 순간→첫 글자 ms."""
    s, d, _ = runs_setup(tmp_path, fake_cli)
    app = create_app(s, deps_factory=lambda _s: d)
    async with client(app) as c:
        r = await c.post("/api/ask", json={"question": "가짜 질문", "columns": ["b", "c"], "b_on": False})
        rid = r.json()["run_id"]
        ev = parse_sse((await c.get(f"/api/runs/{rid}/events")).text)
        sd = _start_done(ev)
        assert [k for k, _ in sd[:2]] == ["start", "start"]  # 두 열 다 시작한 뒤에야 끝남이 온다
        done = {x["column"]: x for k, x in ev if k == "done"}
        for col in ("b", "c"):
            v = done[col]["click_to_first_text_ms"]
            assert isinstance(v, float) and v >= 0 and done[col]["speed"]["click_to_first_text_ms"] == v
        rows = RunStore(tmp_path / "runs").runs()
        assert len(rows) == 1 and rows[0]["run_id"] == rid and rows[0]["run_mode"] == "concurrent"
        assert all(isinstance(rows[0]["columns"][k]["timing"]["click_to_first_text_ms"], float) for k in ("b", "c"))
        t = (await c.post("/api/speed/table", json={"run_ids": [rid]})).json()
        assert t["stats"]["c"]["click_to_first_text_ms"]["n"] == 1


async def test_runner_default_is_sequential(tmp_path, fake_cli):
    """CLI runner · batch 기본 = 차례로(한 열이 끝나야 다음 열 시작) · 누른 순간 값 없음."""
    from clinic_chat import progress

    s, d, _ = runs_setup(tmp_path, fake_cli)
    evs: list[dict] = []
    with progress.listen(evs.append):
        r = await run_question("가짜 질문", d, columns=("b", "c"), b_on=False, store=RunStore(tmp_path / "runs"),
                               seed=1)
    kinds = [e["event"] for e in evs if e["event"] in ("start", "column_done")]
    assert kinds == ["start", "column_done", "start", "column_done"]
    assert r["run_mode"] == "sequential"
    assert all("click_to_first_text_ms" not in r["columns"][k]["timing"] for k in ("b", "c"))
