"""실험 서버 — FastAPI · SSE · 정적 파일(127.0.0.1 에만 바인딩 · SPEC-006 §2-1 · §4-3).

`POST /api/ask {question}` → `{run_id}` · `GET /api/runs/{run_id}/events`(SSE) · `GET /api/runs?limit=` ·
`POST /api/runs/{run_id}/human_check` · `POST /api/runs/{run_id}/rerun`(R-8 · 3회 차례로) ·
`GET /api/runs/{run_id}`(기록에서 다시 그리기용) · `GET /api/status`(기동 확인 · 키 있음/없음).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from clinic_chat import runner
from clinic_chat.clients import anthropic_client, jev_client
from clinic_chat.clients.hub_mcp import HubMCP
from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.config import ROOT, Settings, get_settings
from clinic_chat.deps import NO_KEY, Deps
from clinic_chat.runs import RunStore, app_commit

WEB = ROOT / "web"
RERUN_TIMES = 3


@dataclass
class RunState:
    run_id: str
    question: str
    events: list[tuple[str, dict]] = field(default_factory=list)
    done: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def push(self, event: str, data: dict) -> None:
        self.events.append((event, data))
        if event == "run_done":
            self.done = True
        ev, self.changed = self.changed, asyncio.Event()
        ev.set()


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class CheckIn(BaseModel):
    case: str
    verdict: str
    note: str = ""
    item: str | None = None


def build_deps(settings: Settings) -> Deps:
    from clinic_chat.quality.normalize import load_rules, make_normalizer
    from clinic_chat.quality.raw_tools import RawStore

    deps = Deps(settings=settings, anthropic=anthropic_client.make_client(settings),
                jev=jev_client.make_client(settings), gold_mcp=HubMCP(settings.hub_gold_mcp_url),
                raw_mcp=HubMCP(settings.hub_raw_mcp_url), hub_rest=HubRest(settings.hub_rest_url))
    if settings.raw_dir:
        try:
            deps.raw_store = RawStore(settings.raw_dir)
        except Exception as e:  # noqa: BLE001 — 기동 확인 줄에 사유만
            deps.startup["raw_dir_error"] = str(e)
    if settings.semantic_json:
        try:
            import json as _json
            from pathlib import Path

            deps.normalizer = make_normalizer(load_rules(settings.semantic_json))
            deps.semantic = _json.loads(Path(settings.semantic_json).read_text(encoding="utf-8"))["dictionary"]
        except Exception as e:  # noqa: BLE001 — 기동 확인 줄에 사유만
            deps.startup["semantic_error"] = str(e)
    return deps


async def startup_check(deps: Deps) -> dict:
    """기동 확인 줄 — 허브 골드 · 원장 MCP 연결 · vocab 캐시 셈 · 모델 id · 키 있음/없음(값 출력 금지)."""
    info: dict[str, Any] = dict(deps.startup)
    s = deps.settings
    info["keys"] = {"anthropic": s.has_anthropic_key or deps.anthropic is not None,
                    "typesafe": s.has_typesafe_key or deps.jev is not None}
    info["cases"] = {"case1": None if deps.anthropic else NO_KEY, "case2": None if deps.anthropic else NO_KEY,
                     "case3": None if (deps.anthropic and deps.jev) else NO_KEY}
    try:
        h = await deps.hub_rest.health()
        info["hub_health"] = {"build": (h.get("build") or {}).get("id"), "raw": h.get("raw")}
    except Exception as e:  # noqa: BLE001 — 기동 확인 줄에 사유만
        info["hub_health"] = f"연결 안 됨: {type(e).__name__}"
    for name, m in (("gold_mcp", deps.gold_mcp), ("raw_mcp", deps.raw_mcp)):
        try:
            async with m.session() as sess:
                info[name] = len(await sess.list_tools())
        except Exception as e:  # noqa: BLE001 — 기동 확인 줄에 사유만
            info[name] = f"연결 안 됨: {type(e).__name__}"
    try:
        deps.vocab = await deps.hub_rest.vocab()
        v = deps.vocab.get("vocab", deps.vocab)
        info["vocab"] = {k: len(x) for k, x in v.items() if isinstance(x, list)}
    except Exception as e:  # noqa: BLE001 — 기동 확인 줄에 사유만
        info["vocab"] = f"캐시 실패: {type(e).__name__}"
    if deps.anthropic is not None and hasattr(deps.anthropic, "models"):
        try:
            ids = {m.id async for m in deps.anthropic.models.list(limit=100)}
            info["models"] = {m: (m in ids) for m in {s.answer_model, s.judge_model, s.extract_model}}
        except Exception as e:  # noqa: BLE001 — 기동 확인 줄에 사유만
            info["models"] = f"확인 실패: {type(e).__name__}"
    info["raw_store"] = deps.raw_store.dir_manifest_sha16 if deps.raw_store else "없음(RAW_DIR)"
    info["semantic"] = "있음" if deps.normalizer else "없음(SEMANTIC_JSON)"
    return info


def create_app(deps: Deps | None = None, *, check: bool = True, before_start: Any = None) -> FastAPI:
    """deps 를 주면 그것을 씀(시험 · 모의 판). before_start = 기동 전에 같은 이벤트 루프에서 부를 async 함수."""
    state: dict[str, Any] = {"deps": deps, "runs": {}, "lock": asyncio.Lock()}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if before_start:
            await before_start()
        d = state["deps"] or build_deps(get_settings())
        d.app_commit = d.app_commit or app_commit()
        state["deps"] = d
        state["store"] = RunStore(d.settings.runs_path)
        state["status"] = await startup_check(d) if check else {}
        print(f"[clinic_chat] {d.settings.summary_line()}", flush=True)
        print(f"[clinic_chat] 기동 확인 {json.dumps(state['status'], ensure_ascii=False)}", flush=True)
        yield

    app = FastAPI(title="clinic_chat hub-evals", lifespan=lifespan)

    async def start_run(question: str, extra: dict | None = None, run_id: str | None = None) -> RunState:
        rs = RunState(run_id or runner.new_run_id(), question)
        state["runs"][rs.run_id] = rs

        async def go():
            try:
                async with state["lock"]:  # 질문은 한 번에 하나(속도 측정이 서로 섞이지 않게)
                    rec = await runner.execute(state["deps"], question, rs.push, run_id=rs.run_id)
                rec.update(extra or {})
                state["store"].append_run(rec)
            except Exception as e:  # noqa: BLE001 — 화면에 오류 이벤트로
                rs.push("error", {"message": f"{type(e).__name__}: {e}"[:500]})
            finally:
                if not rs.done:
                    rs.push("run_done", {"run_id": rs.run_id})

        asyncio.create_task(go())
        return rs

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/api/status")
    async def status() -> dict:
        return state["status"]

    @app.post("/api/ask")
    async def ask(body: AskIn) -> dict:
        rs = await start_run(body.question.strip())
        return {"run_id": rs.run_id}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str):
        rs: RunState | None = state["runs"].get(run_id)
        if rs is None:
            raise HTTPException(404, "실행 중인 run 아님 — 기록은 GET /api/runs/{run_id}")

        async def gen():
            i = 0
            while True:
                waiter = rs.changed
                batch, i = rs.events[i:], len(rs.events)
                for e, d in batch:
                    yield {"event": e, "data": json.dumps(d, ensure_ascii=False)}
                if rs.done and i >= len(rs.events):
                    return
                if i >= len(rs.events):
                    await waiter.wait()

        return EventSourceResponse(gen())

    @app.get("/api/runs")
    async def list_runs(limit: int = 50) -> dict:
        return {"runs": state["store"].list(limit=max(1, min(limit, 500)))}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict:
        rec = state["store"].get(run_id)
        if rec is None:
            raise HTTPException(404, "기록 없음")
        checks = [c for c in state["store"].checks() if c["run_id"] == run_id]
        return {"record": rec, "human_checks": checks}

    @app.post("/api/runs/{run_id}/human_check")
    async def human_check(run_id: str, body: CheckIn) -> dict:
        if body.case not in runner.CASES:
            raise HTTPException(422, "case 는 case1 · case2 · case3")
        try:
            return state["store"].add_human_check(run_id, body.case, body.verdict, body.note, body.item)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/runs/{run_id}/rerun")
    async def rerun(run_id: str) -> dict:
        rec = state["store"].get(run_id)
        q = rec["question"] if rec else (state["runs"][run_id].question if run_id in state["runs"] else None)
        if q is None:
            raise HTTPException(404, "기록 없음")
        ids = [runner.new_run_id() for _ in range(RERUN_TIMES)]
        for rid in ids:  # 차례로(lock) — 같은 질문 3회 · 화면이 중간값
            await start_run(q, {"rerun_of": run_id}, run_id=rid)
        return {"run_ids": ids, "question": q}

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def main() -> None:
    s = get_settings()
    uvicorn.run(create_app(), host="127.0.0.1", port=s.clinic_chat_port, log_level="info")


if __name__ == "__main__":
    main()
