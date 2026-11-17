"""상담 화면 서버(SPEC-004 §4-1 · WORK-011 §6 · §12) — FastAPI · 127.0.0.1 만 · 기본 포트 `CHAT_PORT`(18222).

라우트는 `HX-Request` 면 조각 · 아니면 전체 페이지(DAY 규칙). JSON 을 내는 라우트는 없다.
턴은 서버 작업으로 돈다 — 줄기(`/c/{conv_id}/turns/{n}/events`)는 `Live` 를 읽기만 한다(W-6 · 취소 없음).
POST 에 CSRF 를 걸지 않는다 — 이 판은 로그인 · 세션 쿠키가 없고 127.0.0.1 한 사람이다(B-9 · 배포 전에 닫는다).

    uv run python -m clinic_chat.consult [--port N]
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Form, Header, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from clinic_chat.columns.base import Deps
from clinic_chat.config import Settings, load_settings
from clinic_chat.consult import prompt as prompt_mod
from clinic_chat.consult.ledger import Conv, Ledger, blocked, state_at_start
from clinic_chat.consult.live import Live, resume_from, stream
from clinic_chat.consult.render import Render
from clinic_chat.consult.sites import all_dots
from clinic_chat.consult.turn import Ctx, run_turn
from clinic_chat.llm.chat import ChatLLM
from clinic_chat.llm.chat_cli import ChatCli, ChatCliSpec

HOST = "127.0.0.1"
STATIC = Path(__file__).parent / "static"

TurnFn = Callable[..., Awaitable[None]]


def turn_view(conv: Conv, live: dict[tuple[str, int], Live]) -> list[dict]:
    out = []
    for t in conv.turns:
        err = t.get("error")
        kind = None
        if err and (t.get("llm") or {}).get("answered") is not True:
            kind = (err.get("llm") or err).get("kind") if isinstance(err, dict) else "internal"
        out.append({"n": t["turn"], "question": t.get("question") or "", "live": False, "answer": t.get("answer") or "",
                    "end_state": t.get("end_state"), "error_kind": kind if not t.get("answer") else None})
    for n in conv.open_turns():
        lv = live.get((conv.conv_id, n))
        out.append({"n": n, "question": conv.starts[n].get("question") or "", "live": lv is not None,
                    "answer": "", "end_state": None, "error_kind": None if lv is not None else "broken"})
    out.sort(key=lambda x: x["n"])
    return out


def create_app(settings: Settings | None = None, *, turn_fn: TurnFn | None = None, llm: ChatLLM | None = None,
               deps_factory: Callable[[Settings], Deps] = Deps.from_settings) -> FastAPI:
    s = settings or load_settings()
    labels = prompt_mod.load(s.chat_prompt_path()).steps  # 꼴이 틀리면 시작 실패(T19) · 단계 문구는 지금 파일 것
    if turn_fn is None:
        if s.chat_fake_turn:
            from clinic_chat.consult.fake_turn import make

            turn_fn = make()
        else:
            turn_fn = run_turn
    ctx = Ctx(s, Ledger(s.chat_dir_path()), Render(labels), llm or ChatCli(ChatCliSpec.from_settings(s)), deps_factory)
    live: dict[tuple[str, int], Live] = {}
    tasks: set[asyncio.Task] = set()
    app = FastAPI(title="clinic_chat consult")

    def conv_of(conv_id: str) -> Conv:
        c = ctx.ledger.get(conv_id)
        if c is None:
            c = ctx.ledger.new_conv(None, conv_id)
        return c

    def ensure_prompt(c: Conv) -> None:
        """새 대화 = 지금 파일(보관본을 남김) · 되살린 대화 = 시작 때 보관본(B-3)."""
        if c.prompt is not None:
            return
        if c.prompt_sha:
            c.prompt = prompt_mod.kept(s.chat_dir_path(), c.prompt_sha)
        else:
            c.prompt = prompt_mod.keep(s.chat_prompt_path(), s.chat_dir_path())
            c.prompt_sha = c.prompt.sha256

    def page(c: Conv, hx: bool) -> HTMLResponse:
        html = ctx.render.page(c.conv_id, turn_view(c, live), c.last_list(), c.last_sites(), fragment=hx)  # 0.2 꼴도 같은 길
        return HTMLResponse(html)

    async def drive(c: Conv, n: int, question: str, clicked_at: float) -> None:
        lv = live[(c.conv_id, n)]
        try:
            await turn_fn(ctx, c, n, question, lv.push, clicked_at=clicked_at)
        except Exception as e:  # noqa: BLE001 — 그 턴만 오류
            lv.push("turn_error", ctx.render.error(n, "internal"))
            if c.running == n:
                ctx.ledger.end(c, {"turn": n, "question": question, "session_state": c.chat.state,
                                   "error": {"kind": "internal", "stage": "turn", "detail": type(e).__name__},
                                   "llm": {"answered": False}})
        finally:
            if c.running == n:
                ctx.ledger.end(c, {"turn": n, "question": question, "session_state": c.chat.state,
                                   "error": {"kind": "internal", "stage": "turn", "detail": "no_end_row"},
                                   "llm": {"answered": False}})
            lv.finish()

    @app.get("/")
    async def index() -> RedirectResponse:
        """새 대화 → `303 /c/<새 id>`(검수 W-1) — 주소가 대화를 가리켜 새로 고침 · 서버 재시작 뒤에도 같은 대화를 되읽는다."""
        c = ctx.ledger.new_conv()
        return RedirectResponse(f"/c/{c.conv_id}", status_code=303)

    @app.get("/c/{conv_id}", response_class=HTMLResponse)
    async def conversation(conv_id: str, hx_request: str | None = Header(None)) -> HTMLResponse:
        return page(conv_of(conv_id), bool(hx_request))

    @app.post("/c/{conv_id}/turns", response_class=HTMLResponse)
    async def post_turn(conv_id: str, text: str = Form("")) -> HTMLResponse:
        clicked_at = time.perf_counter()
        c = conv_of(conv_id)
        q = (text or "").strip()
        if not q:
            return HTMLResponse(ctx.render.rejected("empty"))
        why = blocked(c, s.chat_max_turns)
        if why:
            return HTMLResponse(ctx.render.rejected(why, q))
        state = state_at_start(c, s.chat_state)  # 0.2 — 대화 첫 턴의 값으로 끝까지(WORK-012 §6)
        try:
            ensure_prompt(c)
            if state == "on" and not c.prompt.state_ready:  # §9a — 켠 대화는 0.2 키가 다 있어야 시작한다
                raise prompt_mod.PromptError("state_keys")
        except prompt_mod.PromptError:
            return HTMLResponse(ctx.render.rejected("prompt", q))
        n = ctx.ledger.start(c, q, state)["turn"]
        live[(c.conv_id, n)] = Live()
        t = asyncio.create_task(drive(c, n, q, clicked_at))
        tasks.add(t)
        t.add_done_callback(tasks.discard)
        return HTMLResponse(ctx.render.turn(c.conv_id, {"n": n, "question": q, "live": True}))

    @app.get("/c/{conv_id}/turns/{n}/events")
    async def events(conv_id: str, n: int, last_event_id: str | None = Header(None)) -> Response:
        lv = live.get((conv_id, n))
        if lv is None:
            return Response(status_code=204)  # 없는 줄기 — 브라우저가 다시 붙지 않는다
        return StreamingResponse(stream(lv, resume_from(last_event_id)), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/face-check", response_class=HTMLResponse)
    async def face_check() -> HTMLResponse:
        return HTMLResponse(ctx.render.face_check(all_dots()))

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.state.ctx = ctx
    app.state.live = live
    app.state.tasks = tasks
    return app


def main(host: str = HOST, port: int | None = None) -> None:
    import uvicorn

    s = load_settings()
    uvicorn.run(create_app(s), host=host, port=port or s.chat_port, log_level="info")
