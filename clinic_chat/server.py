"""실험 서버 — FastAPI · SSE · 정적 파일(127.0.0.1 에만 바인딩)."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from clinic_chat.config import ROOT, get_settings

WEB = ROOT / "web"


def create_app() -> FastAPI:
    app = FastAPI(title="clinic_chat hub-evals")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def main() -> None:
    s = get_settings()
    print(f"[clinic_chat] {s.summary_line()}", flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=s.clinic_chat_port, log_level="info")


if __name__ == "__main__":
    main()
