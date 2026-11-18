"""시험 화면 서버(WORK-003) — FastAPI · 127.0.0.1:18220 만.

걷기 · 답 · 판정 로직은 없다 — `POST /api/ask` 는 CLI 와 같은 `runner.run_question` 을 부르고(기록도 같은 `runs.jsonl`),
`clinic_chat.progress` 로 받은 이벤트를 SSE 로 옮길 뿐이다.

    uv run python -m clinic_chat.web
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from clinic_chat import progress, prompts, speed
from clinic_chat.columns.base import Deps
from clinic_chat.config import Settings, load_settings
from clinic_chat.runner import ALL_COLS, COLS, run_question
from clinic_chat.runs import RunStore, show_view

HOST = "127.0.0.1"
PORT = 18220
STATIC = Path(__file__).parent / "static"

RunFn = Callable[..., Awaitable[dict]]
Effort = Literal["low", "medium", "high"]  # 속도 탭 effort 고르기(CLI `--effort` 값)


class SpeedTableBody(BaseModel):
    run_ids: list[str]
    effort: Effort | None = None  # 주면 기록의 열 effort 가 이 값과 다른 run_id 를 `effort_mismatch` 로 알린다


class AskBody(BaseModel):
    question: str | None = None
    set_id: str | None = None
    columns: list[str] = list(COLS)
    b_on: bool = False  # O-26 ① — 화면 기본 열에서 B 켬을 뺀다(고르면 켬)
    effort: Effort | None = None  # 없으면 설정 그대로(정확도 탭) · 주면 모든 열 같은 값(속도 탭)


@dataclass
class Live:
    """한 실행의 이벤트 — 늦게 붙은 구독자도 처음부터 받는다."""

    events: list[dict] = field(default_factory=list)
    finished: bool = False
    done_cols: set[str] = field(default_factory=set)
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def push(self, ev: dict) -> None:
        self.events.append(ev)
        self.wake.set()


def done_payload(column: str | None, result: dict | None, model: str | None, effort: str | None,
                 question: str | None = None) -> dict:
    """열 하나의 `done` — 종료 상태 · ms · 호출 수 · 모델 id · effort · 오류(값은 열 결과에서 옮길 뿐).

    `speed` = 속도 탭 카드 머리 값(`clinic_chat.speed.card` — WORK-004)."""
    r = result or {}
    return {"column": column, "ran": result is not None, "end_state": r.get("end_state"),
            "total_ms": (r.get("timing") or {}).get("total_ms"), "ttft_ms": (r.get("timing") or {}).get("ttft_ms"),
            "click_to_first_text_ms": (r.get("timing") or {}).get("click_to_first_text_ms"),
            "click_to_first_answer_ms": (r.get("timing") or {}).get("click_to_first_answer_ms"),
            "first_answer_text_ms": (r.get("timing") or {}).get("first_answer_text_ms"),
            "calls": r.get("calls") or {},
            "model": model, "effort": effort, "error": r.get("error"), "cited": len(r.get("cited") or []),
            "speed": speed.card(column or "", result, model=model, effort=effort, question=question)}


def to_sse(ev: dict, i: int) -> str:
    """`id:` = 이벤트 번호 — EventSource 가 다시 붙을 때 `Last-Event-ID` 로 돌려준다."""
    kind = ev.get("event", "message")
    data = {k: v for k, v in ev.items() if k != "event"}
    return f"id: {i}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def resume_from(last_event_id: str | None) -> int:
    """다시 붙은 구독자는 이미 받은 번호 다음부터(답 중복 0) · 없거나 이상하면 처음부터."""
    try:
        return max(0, int(last_event_id) + 1) if last_event_id is not None else 0
    except ValueError:
        return 0


def run_summary(row: dict, graded: set[str]) -> dict:
    cols = {}
    for k, c in (row.get("columns") or {}).items():
        if c:
            cols[k] = {"end_state": c.get("end_state"), "total_ms": (c.get("timing") or {}).get("total_ms"),
                       "error": bool(c.get("error"))}
    return {"run_id": row.get("run_id"), "asked_at": row.get("asked_at"), "set_id": row.get("set_id"),
            "batch_id": row.get("batch_id"), "question": row.get("question"), "columns": cols,
            "graded": row.get("run_id") in graded}


def create_app(settings: Settings | None = None, *, run: RunFn = run_question,
               deps_factory: Callable[[Settings], Deps] = Deps.from_settings) -> FastAPI:
    s = settings or load_settings()
    store = RunStore(s.path(s.runs_dir))
    live: dict[str, Live] = {}
    tasks: set[asyncio.Task] = set()
    app = FastAPI(title="clinic_chat")

    def load_set() -> dict[str, dict]:
        from clinic_chat.evalset.loader import load_set as _load

        return _load(s)

    async def drive(run_id: str, question: str, set_id: str | None, cols: tuple[str, ...], b_on: bool,
                    effort: str | None = None, clicked_at: float | None = None) -> None:
        lv = live[run_id]
        # 화면 = 고른 열 동시 시작 — CLI 동시 상한은 설정값과 고른 열 수 중 큰 쪽(설정값을 낮추지는 않는다)
        sc = s.model_copy(update={"cli_max_concurrent": max(s.cli_max_concurrent, len(cols))})

        def sink(ev: dict) -> None:
            kind = ev.get("event")
            if kind == "column_done":
                col = ev.get("column")
                if col in lv.done_cols:  # B 는 col_b 가 먼저 알리고 runner 가 같은 값으로 한 번 더 — 처음 것만
                    return
                lv.done_cols.add(col)
                lv.push({"event": "done", **done_payload(col, ev.get("result"), ev.get("model"), ev.get("effort"),
                                                         question)})
            elif kind == "run_done":
                return
            else:
                lv.push(ev)

        try:
            with progress.listen(sink):
                await run(question, deps_factory(sc), columns=cols, set_id=set_id, b_on=b_on, store=store,
                          run_id=run_id, effort=effort, concurrent=True, deps_for=lambda _c: deps_factory(sc),
                          clicked_at=clicked_at)
            lv.push({"event": "end", "run_id": run_id, "error": None})
        except Exception as e:  # noqa: BLE001 — 실행 자체가 못 돈 것(열 오류는 runner 가 열에 적는다)
            lv.push({"event": "end", "run_id": run_id, "error": {"kind": type(e).__name__, "detail": str(e)[:300]}})
        finally:
            lv.finished = True
            lv.wake.set()

    @app.post("/api/ask")
    async def ask(body: AskBody) -> dict:
        clicked_at = time.perf_counter()  # 화면 요청을 받은 순간 — 열마다 첫 글자까지의 기준
        cols = tuple(body.columns)
        bad = [c for c in cols if c not in ALL_COLS]
        if bad or not cols:
            raise HTTPException(400, f"columns 는 {','.join(ALL_COLS)} 중에서 — 받은 것 {list(body.columns)!r}")
        if bool(body.question) == bool(body.set_id):
            raise HTTPException(400, "question 또는 set_id 하나")
        q, sid = body.question, None
        if body.set_id:
            item = load_set().get(body.set_id)
            if item is None:
                raise HTTPException(404, f"세트에 없는 id: {body.set_id}")
            if item.get("status") == "draft":
                raise HTTPException(400, f"{body.set_id} 는 draft(사용자 확정 전) — Jev · LLM 으로 보내지 않는다")
            q, sid = item.get("question"), body.set_id
            if not q:
                raise HTTPException(400, f"{body.set_id} 질문 문장 없음")
        run_id = uuid.uuid4().hex[:12]
        live[run_id] = Live()
        t = asyncio.create_task(drive(run_id, q, sid, cols, body.b_on, body.effort, clicked_at))
        tasks.add(t)
        t.add_done_callback(tasks.discard)
        return {"run_id": run_id, "columns": list(cols), "b_on": body.b_on and "b" in cols,
                "question": q, "set_id": sid, "effort": body.effort}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, last_event_id: str | None = Header(None)) -> StreamingResponse:
        lv = live.get(run_id)
        if lv is None:
            raise HTTPException(404, f"run_id 없음: {run_id}")

        async def gen() -> AsyncIterator[str]:
            i = resume_from(last_event_id)
            while True:
                lv.wake.clear()
                while i < len(lv.events):
                    yield to_sse(lv.events[i], i)
                    i += 1
                if lv.finished and i >= len(lv.events):
                    return
                try:
                    await asyncio.wait_for(lv.wake.wait(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs")
    async def runs(limit: int = 30) -> dict:
        graded = {q.get("run_id") for q in store.quality()}
        rows = store.runs()[-max(1, min(limit, 500)):]
        return {"runs": [run_summary(r, graded) for r in reversed(rows)]}

    @app.get("/api/runs/{run_id}")
    async def run_show(run_id: str, column: str | None = None, trace: bool = False) -> dict:
        row = store.get(run_id)
        if not row:
            raise HTTPException(404, f"run_id 없음: {run_id}")
        quality = [q for q in store.quality() if q.get("run_id") == run_id]
        return {"view": show_view(row, column, trace), "row": row, "quality": quality}

    @app.get("/api/speed/{run_id}")
    async def speed_one(run_id: str) -> dict:
        """속도 탭 — 한 기록의 카드 값(B · C · B 전체)."""
        row = store.get(run_id)
        if not row:
            raise HTTPException(404, f"run_id 없음: {run_id}")
        return {"run_id": run_id, "set_id": row.get("set_id"), "question": row.get("question"),
                "cards": speed.row_cards(row)}

    @app.post("/api/speed/table")
    async def speed_table(body: SpeedTableBody) -> dict:
        """속도 탭 「세트 몇 문항 한꺼번에」 — 문항 × 열 표 + 평균 · 중앙값(기록에서 읽는다)."""
        rows = [r for r in (store.get(x) for x in body.run_ids) if r]
        out = speed.table(rows)
        if body.effort:
            out["effort"] = body.effort
            out["effort_mismatch"] = [r.get("run_id") for r in rows
                                      if any(c.get("effort") != body.effort for c in speed.row_cards(r).values())]
        return out

    @app.get("/api/set")
    async def set_items() -> dict:
        items = [{"id": it.get("id"), "range": it.get("range"), "bundle": it.get("bundle"),
                  "status": it.get("status"), "expected_end_state": it.get("expected_end_state"),
                  "question": it.get("question")} for it in load_set().values()]
        return {"items": items}

    @app.get("/api/meta")
    async def meta() -> dict:
        gold = None
        try:
            gold = ((await deps_factory(s).hub.health()).get("build") or {}).get("gold_hash")
        except Exception:  # noqa: BLE001 — 허브가 안 떠 있어도 화면은 뜬다
            gold = None
        st = prompts.status(s)
        return {"prompts_mark": st["mark"], "answer_reasoning": s.answer_reasoning, "lowest_effort": s.lowest_effort,
                "effort": s.claude_cli_effort, "gold_hash": gold, "columns": list(COLS),
                "speed_columns": ["b", "c", "b_full"]}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.state.live = live
    app.state.tasks = tasks
    return app


def main(host: str = HOST, port: int = PORT) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, log_level="info")
