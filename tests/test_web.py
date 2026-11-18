"""WORK-003 — 시험 화면 서버(모의 runner · 모의 SSE · 모의 CLI — 실호출 0)."""

from __future__ import annotations

import json

import httpx

from clinic_chat import progress
from clinic_chat.columns.base import column
from clinic_chat.runs import RunStore
from clinic_chat.web import server
from clinic_chat.web.server import create_app
from tests.conftest import make_settings
from tests.test_runs import setup as runs_setup


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        kind, data = None, None
        for ln in block.splitlines():
            if ln.startswith("event: "):
                kind = ln[7:]
            elif ln.startswith("data: "):
                data = json.loads(ln[6:])
        if kind:
            out.append((kind, data))
    return out


class NoHub:
    async def health(self):
        raise RuntimeError("hub down")


class FakeDeps:
    hub = NoHub()


def mock_run(calls: list):
    """runner.run_question 과 같은 인자를 받는 모의 — 알림을 내고 줄을 쓴다(걷기 없음)."""

    async def run(question, d, *, columns, set_id, b_on, store, run_id, effort=None, concurrent=False,
                  deps_for=None, clicked_at=None):
        calls.append({"question": question, "columns": columns, "set_id": set_id, "b_on": b_on})
        cols = {}
        for c in columns:
            with progress.column(c):
                progress.emit("start", effort="medium")
                if c == "b":
                    for st in ("①", "②", "③", "④", "⑤"):
                        progress.emit("step", step=st, stage="x")
                progress.emit("delta", text="가짜")
                progress.emit("delta", text=" 답")
                cols[c] = column("가짜 답", end_state="근거 충족" if c == "b" else None,
                                 timing={"total_ms": 12.0}, calls={"llm_round_trips": 1})
                progress.emit("column_done", result=cols[c], model="claude-test", effort="medium")
                if c == "b":  # col_b 가 먼저 · runner 가 한 번 더(같은 값) — 서버는 처음 것만
                    progress.emit("column_done", result=cols[c], model="claude-test", effort="medium")
        row = {"run_id": run_id, "asked_at": "2026-11-16T00:00:00+09:00", "set_id": set_id, "question": question,
               "settings": {}, "models": {}, "columns": {k: cols.get(k) for k in ("a", "b", "b_on", "c")}}
        store.append_run(row)
        progress.emit("run_done", column=None, run_id=run_id)
        return row

    return run


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def write_set(s, items):
    p = s.path(s.eval_set_dir)
    p.mkdir(parents=True, exist_ok=True)
    (p / "set.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items), encoding="utf-8")


def test_port_is_18220_local_only():
    assert server.HOST == "127.0.0.1" and server.PORT == 18220


async def test_ask_streams_and_records(tmp_path):
    s = make_settings(tmp_path)
    calls: list = []
    app = create_app(s, run=mock_run(calls), deps_factory=lambda _s: FakeDeps())
    async with client(app) as c:
        r = await c.post("/api/ask", json={"question": "가짜 질문", "columns": ["b", "c"], "b_on": True})
        assert r.status_code == 200
        rid = r.json()["run_id"]
        ev = parse_sse((await c.get(f"/api/runs/{rid}/events")).text)
        kinds = [k for k, _ in ev]
        assert kinds[0] == "start" and kinds[-1] == "end"
        assert [d["text"] for k, d in ev if k == "delta" and d["column"] == "b"] == ["가짜", " 답"]
        assert [d["step"] for k, d in ev if k == "step"] == ["①", "②", "③", "④", "⑤"]
        done = [d for k, d in ev if k == "done"]
        assert [d["column"] for d in done] == ["b", "c"]  # B 두 번 알림 → 한 번
        b = done[0]
        assert b["end_state"] == "근거 충족" and b["total_ms"] == 12.0 and b["model"] == "claude-test"
        assert b["effort"] == "medium" and b["calls"]["llm_round_trips"] == 1 and b["error"] is None
        assert calls == [{"question": "가짜 질문", "columns": ("b", "c"), "set_id": None, "b_on": True}]
        # 기록 = 같은 runs.jsonl
        assert RunStore(s.path(s.runs_dir)).get(rid)["question"] == "가짜 질문"
        lst = (await c.get("/api/runs?limit=5")).json()["runs"]
        assert lst[0]["run_id"] == rid and lst[0]["columns"]["b"]["end_state"] == "근거 충족"
        show = (await c.get(f"/api/runs/{rid}")).json()
        assert show["row"]["run_id"] == rid and show["quality"] == []
        assert set(show["view"]["columns"]["b"]) == {"answer", "end_state", "error", "cited"}
        assert (await c.get(f"/api/runs/{rid}?column=b&trace=true")).json()["view"]["end_state"] == "근거 충족"
        assert (await c.get("/api/runs/nope")).status_code == 404


async def test_ask_validation_and_set(tmp_path):
    s = make_settings(tmp_path)
    write_set(s, [{"id": "X01", "range": "시술", "status": "confirmed", "question": "가짜 질문 하나"},
                  {"id": "X02", "range": "고민", "status": "draft", "question": "가짜 질문 둘"}])
    calls: list = []
    app = create_app(s, run=mock_run(calls), deps_factory=lambda _s: FakeDeps())
    async with client(app) as c:
        assert (await c.post("/api/ask", json={"question": "q", "columns": ["B"]})).status_code == 400
        assert (await c.post("/api/ask", json={"question": "q", "columns": []})).status_code == 400
        assert (await c.post("/api/ask", json={"columns": ["b"]})).status_code == 400
        assert (await c.post("/api/ask", json={"set_id": "X02", "columns": ["b"]})).status_code == 400  # draft
        assert (await c.post("/api/ask", json={"set_id": "X99", "columns": ["b"]})).status_code == 404
        r = await c.post("/api/ask", json={"set_id": "X01", "columns": ["b"], "b_on": False})
        assert r.status_code == 200 and r.json()["b_on"] is False
        await c.get(f"/api/runs/{r.json()['run_id']}/events")
        assert calls[-1]["question"] == "가짜 질문 하나" and calls[-1]["set_id"] == "X01"
        items = (await c.get("/api/set")).json()["items"]
        assert {(x["id"], x["status"]) for x in items} == {("X01", "confirmed"), ("X02", "draft")}
        m = (await c.get("/api/meta")).json()
        assert m["gold_hash"] is None and m["prompts_mark"] and m["lowest_effort"] == s.lowest_effort
        assert (await c.get("/")).status_code == 200
        assert (await c.get("/static/app.js")).status_code == 200


async def test_real_runner_over_fake_cli(tmp_path, fake_cli):
    """runner.run_question 그대로(모의 CLI · 모의 허브 · 모의 Jev) — delta 가 CLI stream-json 에서 오고 done 은 기록과 같다."""
    s, d, _ = runs_setup(tmp_path, fake_cli)
    app = create_app(s, deps_factory=lambda _s: d)
    async with client(app) as c:
        rid = (await c.post("/api/ask", json={"question": "가짜 질문", "columns": ["b"], "b_on": True})).json()["run_id"]
        ev = parse_sse((await c.get(f"/api/runs/{rid}/events")).text)
        assert ev[-1] == ("end", {"run_id": rid, "error": None})
        deltas = "".join(x["text"] for k, x in ev if k == "delta" and x["column"] == "b")
        row = RunStore(s.path(s.runs_dir)).get(rid)
        assert deltas and deltas == row["columns"]["b"]["answer"]
        done = {x["column"]: x for k, x in ev if k == "done"}
        assert done["b"]["end_state"] == row["columns"]["b"]["end_state"]
        assert done["b"]["effort"] == s.lowest_effort  # 단계 ⑤ = 가장 낮은 effort
        assert ("b_on" in done) and done["b_on"]["ran"] == (row["columns"]["b_on"] is not None)
        assert any(k == "step" and x["step"] == "⑤" for k, x in ev)


def sse_ids(text: str) -> list[int]:
    return [int(ln[4:]) for ln in text.splitlines() if ln.startswith("id: ")]


async def test_sse_ids_and_resume_from_last_event_id(tmp_path):
    """이벤트마다 `id:` · 다시 붙으면(Last-Event-ID) 그 다음 번호부터 — 답 조각이 두 번 오지 않는다."""
    s = make_settings(tmp_path)
    app = create_app(s, run=mock_run([]), deps_factory=lambda _s: FakeDeps())
    async with client(app) as c:
        rid = (await c.post("/api/ask", json={"question": "가짜 질문", "columns": ["a"], "b_on": False})).json()["run_id"]
        full = (await c.get(f"/api/runs/{rid}/events")).text
        ids = sse_ids(full)
        assert ids == list(range(len(ids))) and len(ids) == len(parse_sse(full))
        first_delta = next(i for i, (k, _) in zip(ids, parse_sse(full), strict=True) if k == "delta")
        again = (await c.get(f"/api/runs/{rid}/events", headers={"Last-Event-ID": str(first_delta)})).text
        assert sse_ids(again) == ids[first_delta + 1:]
        assert [d["text"] for k, d in parse_sse(again) if k == "delta"] == [" 답"]  # 「가짜」 는 다시 안 옴
        bad = (await c.get(f"/api/runs/{rid}/events", headers={"Last-Event-ID": "x"})).text
        assert sse_ids(bad) == ids  # 이상한 값이면 처음부터


async def test_ask_returns_sent_question(tmp_path):
    """화면 현재 줄은 서버가 실제로 보낸 글 — 세트면 세트 문장 · 자유 질문이면 그 글(set_id 없음)."""
    s = make_settings(tmp_path)
    write_set(s, [{"id": "X01", "range": "시술", "status": "confirmed", "question": "가짜 질문 하나"}])
    calls: list = []
    app = create_app(s, run=mock_run(calls), deps_factory=lambda _s: FakeDeps())
    async with client(app) as c:
        r = (await c.post("/api/ask", json={"set_id": "X01", "columns": ["a"]})).json()
        assert (r["question"], r["set_id"]) == ("가짜 질문 하나", "X01")
        await c.get(f"/api/runs/{r['run_id']}/events")
        r = (await c.post("/api/ask", json={"question": "가짜 질문 하나 고침", "columns": ["a"]})).json()
        assert (r["question"], r["set_id"]) == ("가짜 질문 하나 고침", None)
        await c.get(f"/api/runs/{r['run_id']}/events")
        assert [(x["question"], x["set_id"]) for x in calls] == [("가짜 질문 하나", "X01"), ("가짜 질문 하나 고침", None)]


# ───── WORK-006 WP-6 — 새 열 목록 · B 켬 기본 끔 · 옛 줄 다시 보기(실호출 0) ─────

async def test_meta_columns_and_b_on_default_off(tmp_path):
    s = make_settings(tmp_path)
    calls: list = []
    app = create_app(s, deps_factory=lambda _s: FakeDeps(), run=mock_run(calls))
    async with client(app) as c:
        meta = (await c.get("/api/meta")).json()
        assert meta["speed_columns"] == ["b", "c", "b_full"] and meta["columns"] == ["a", "b", "c"]
        r = (await c.post("/api/ask", json={"question": "가짜 질문", "columns": ["b"]})).json()
        assert r["b_on"] is False
        assert (await c.post("/api/ask", json={"question": "q", "columns": ["b_slim"]})).status_code == 400
        html = (await c.get("/")).text
        assert 'id="b-on" checked' not in html


async def test_old_row_replay_does_not_break(tmp_path):
    """옛 줄(0.4.1 — `front` 없음 · `range_choice` · `b_slim` 있음)을 다시 볼 때 깨지지 않는다."""
    s = make_settings(tmp_path)
    store = RunStore(s.path(s.runs_dir))
    old = {"run_id": "old1", "asked_at": "2026-11-16T00:00:00+09:00", "set_id": None, "question": "가짜", "settings": {},
           "models": {}, "columns": {"a": None, "b": column("답", end_state="근거 충족", trace={
               "entry": {}, "range_choice": {"rule": "RG2"}, "breadth": [{"tool": "facets", "range": "procedure"}]}),
               "b_on": None, "c": None, "b_slim": column("답", trace={"selection": {"bundles": []}})}}
    store.append_run(old)
    app = create_app(s, deps_factory=lambda _s: FakeDeps())
    async with client(app) as c:
        assert (await c.get("/api/runs/old1?column=b&trace=true")).status_code == 200
        assert (await c.get("/api/speed/old1")).status_code == 200
        assert any(r["run_id"] == "old1" for r in (await c.get("/api/runs")).json()["runs"])
