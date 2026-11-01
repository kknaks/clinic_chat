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

from clinic_chat import batch, runner
from clinic_chat.clients import anthropic_client, jev_client
from clinic_chat.clients.hub_mcp import HubMCP
from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.config import META_MAX_CHARS, ROOT, Settings, get_settings
from clinic_chat.deps import NO_KEY, Deps
from clinic_chat.llm import PROVIDERS, auth, cli
from clinic_chat.llm.specs import describe
from clinic_chat.runs import RunStore, app_commit, now_iso

WEB = ROOT / "web"
RERUN_TIMES = 3


@dataclass
class RunState:
    run_id: str
    question: str
    events: list[tuple[str, dict]] = field(default_factory=list)
    done: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: Any = None

    def push(self, event: str, data: dict) -> None:
        self.events.append((event, data))
        if event == "run_done":
            self.done = True
        ev, self.changed = self.changed, asyncio.Event()
        ev.set()


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    provider: str | None = None  # sdk · claude-cli · codex-cli(없으면 DEFAULT_PROVIDER)


class BatchIn(BaseModel):
    providers: list[str]
    questions: list[int] | None = None


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
    if s.default_provider == "sdk":
        info["cases"] = {"case1": None if deps.anthropic else NO_KEY, "case2": None if deps.anthropic else NO_KEY,
                         "case3": None if (deps.anthropic and deps.jev) else NO_KEY}
    else:  # CLI 기본 — 구독 인증은 판마다 확인(/api/providers) · case3 은 Jev 키만
        info["cases"] = {"case1": None, "case2": None, "case3": None if deps.jev else NO_KEY}
    info["default_provider"] = s.default_provider
    try:
        h = await deps.hub_rest.health()
        info["hub_health"] = {"build": (h.get("build") or {}).get("id"), "build_hash": (h.get("build") or {}).get("build_hash"),
                              "raw": h.get("raw")}
        deps.startup["gold_build_hash"] = (h.get("build") or {}).get("build_hash")
    except Exception as e:  # noqa: BLE001 — 기동 확인 줄에 사유만
        info["hub_health"] = f"연결 안 됨: {type(e).__name__}"
    metas: dict[str, dict] = {}
    for name, m in (("gold_mcp", deps.gold_mcp), ("raw_mcp", deps.raw_mcp)):
        try:
            async with m.session() as sess:
                tools = await sess.list_tools()
                info[name] = len(tools)
                metas.update({f"{name}:{k}": v for k, v in (await sess.list_tools_meta()).items()})
                deps.startup["gold_tools" if name == "gold_mcp" else "raw_tools"] = [t["name"] for t in tools]
                if name == "gold_mcp":
                    deps.section_tools = {t["name"] for t in tools
                                          if "sections" in (t["input_schema"].get("properties") or {})}
                    info["section_tools"] = sorted(deps.section_tools)
                    deps.intent_tools = {t["name"] for t in tools
                                         if "intent" in (t["input_schema"].get("properties") or {})}
                    info["intent_tools"] = sorted(deps.intent_tools)
        except Exception as e:  # noqa: BLE001 — 기동 확인 줄에 사유만
            info[name] = f"연결 안 됨: {type(e).__name__}"
    if metas:  # 허브 도구 결과 크기 meta(SPEC-005 §5-0-2 M-1) — 없으면 경고만(실험은 막지 않음)
        want = s.mcp_result_max_chars
        ok = sum(1 for v in metas.values() if (v or {}).get(META_MAX_CHARS) == want)
        info["tool_meta"] = f"도구 meta {ok}/{len(metas)}" + ("" if ok == len(metas) else
                                                            f" ⚠ {META_MAX_CHARS} ≠ {want} 인 도구 있음(CLI 잘림 가능)")
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
    state: dict[str, Any] = {"deps": deps, "runs": {}, "grading": set()}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if before_start:
            await before_start()
        d = state["deps"] or build_deps(get_settings())
        d.app_commit = d.app_commit or app_commit()
        cli.MAX_CONCURRENT = max(1, d.settings.cli_max_concurrent)  # 동시 CLI 상한(답 · 채점 같이)
        state["deps"] = d
        state["store"] = RunStore(d.settings.runs_path)
        state["status"] = await startup_check(d) if check else {}
        print(f"[clinic_chat] {d.settings.summary_line()}", flush=True)
        print(f"[clinic_chat] 기동 확인 {json.dumps(state['status'], ensure_ascii=False)}", flush=True)
        yield

    app = FastAPI(title="clinic_chat hub-evals", lifespan=lifespan)

    async def start_run(question: str, extra: dict | None = None, run_id: str | None = None,
                        provider: str | None = None) -> RunState:
        rs = RunState(run_id or runner.new_run_id(), question)
        state["runs"][rs.run_id] = rs
        rs.task = asyncio.create_task(run_and_store(rs, question, extra, provider))
        return rs

    async def run_and_store(rs: RunState, question: str, extra: dict | None, provider: str | None) -> dict | None:
        """답 세 열 → 열 하나가 끝나면 그 열 채점을 바로 백그라운드로(자동 채점 판) → 세 열 끝 = 기록 · `answers_done`
        (화면은 여기서 다음 질문 가능) → 남은 채점이 끝나면 `run_done`. 질문 · 채점이 서로 막지 않음(잠금 없음 · CLI 동시 수만 상한)."""
        rec = None
        tasks: list[asyncio.Task] = []
        deps = state["deps"]
        prov = provider or deps.settings.default_provider
        auto = runner.auto_quality(deps.settings, prov) if prov in PROVIDERS else False

        def case_done(c: str, r: dict) -> None:
            if auto and r.get("answer") and not r.get("error"):
                tasks.append(asyncio.create_task(grade_case_task(rs.run_id, c, question, r["answer"], rs.push,
                                                                 meta=extra)))

        try:
            rec = await runner.execute(deps, question, rs.push, run_id=rs.run_id, provider=provider, quality=False,
                                       on_case_done=case_done)
            rec.update(extra or {})
            state["store"].append_run(rec)
            rs.push("answers_done", {"run_id": rs.run_id, "auto_quality": bool(tasks)})
            if tasks:
                await asyncio.gather(*tasks)
                rs.push("quality_done", {"run_id": rs.run_id})
        except Exception as e:  # noqa: BLE001 — 화면에 오류 이벤트로
            rs.push("error", {"message": f"{type(e).__name__}: {e}"[:500]})
        finally:
            if not rs.done:
                rs.push("run_done", {"run_id": rs.run_id})
        return rec

    async def grade_case_task(run_id: str, case: str, question: str, answer: str, emit,
                              meta: dict | None = None) -> dict | None:
        """열 하나 채점 → quality.jsonl 에 그 열 한 줄(기록 줄은 그대로). 자동 · 「채점」 버튼이 같이 씀."""
        key = (run_id, case)
        if key in state["grading"]:
            return None
        state["grading"].add(key)
        try:
            g = await runner.grade_case(state["deps"], question, case, answer, emit)
            entry = {"run_id": run_id, "case": case, "at": now_iso(), "mode": g["mode"],
                     "judge_provider": state["deps"].settings.judge_provider, "cost_usd": g["cost_usd"],
                     "word_check": g["word_check"], "judge": g["judge"],
                     **{k: (meta or {}).get(k) for k in batch.META if k in (meta or {})}}  # 질문 번호 등(참고 답은 채점 안 씀)
            state["store"].add_quality(entry)
            return entry
        except Exception as e:  # noqa: BLE001 — 채점 실패는 그 열 표시만(답 기록은 남음)
            emit("case_quality_error", {"case": case, "message": f"{type(e).__name__}: {e}"[:300]})
            return None
        finally:
            state["grading"].discard(key)

    def grading_any(run_id: str) -> bool:
        return any(k[0] == run_id for k in state["grading"])

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    @app.get("/api/status")
    async def status() -> dict:
        return state["status"]

    @app.post("/api/ask")
    async def ask(body: AskIn) -> dict:
        if body.provider and body.provider not in PROVIDERS:
            raise HTTPException(422, f"provider 는 {' · '.join(PROVIDERS)}")
        rs = await start_run(body.question.strip(), provider=body.provider)
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
        rows = state["store"].list(limit=max(1, min(limit, 100_000)))
        for r in rows:
            r["grading"] = grading_any(r["run_id"])  # 백그라운드 채점 중(화면 기록 표)
        return {"runs": rows}

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

    @app.post("/api/runs/{run_id}/quality")
    async def quality(run_id: str) -> dict:
        """「채점」 버튼 — ① 이름 뽑기 · ② Opus 채점을 이 기록의 답으로 돌려 quality.jsonl 에 덧붙임(기록 줄은 그대로)."""
        rec = state["store"].get_raw(run_id)
        if rec is None:
            raise HTTPException(404, "기록 없음")
        if grading_any(run_id):
            raise HTTPException(409, "채점 중")
        rs = state["runs"].get(run_id)  # 그 질문 SSE 를 보고 있으면 같이 흘려 줌
        emit = rs.push if rs and not rs.done else (lambda e, d: None)
        done = set(state["store"].latest_quality().get(run_id, {}))
        todo = [c for c in runner.gradable(rec["cases"]) if c not in done]
        await asyncio.gather(*(grade_case_task(run_id, c, rec["question"], rec["cases"][c]["answer"], emit) for c in todo))
        return {"record": state["store"].get(run_id)}

    @app.post("/api/runs/{run_id}/rerun")
    async def rerun(run_id: str) -> dict:
        rec = state["store"].get(run_id)
        q = rec["question"] if rec else (state["runs"][run_id].question if run_id in state["runs"] else None)
        provider = (rec or {}).get("provider")
        if q is None:
            raise HTTPException(404, "기록 없음")
        ids = [runner.new_run_id() for _ in range(RERUN_TIMES)]
        for rid in ids:  # 차례로(lock) — 같은 질문 3회 · 화면이 중간값
            await start_run(q, {"rerun_of": run_id}, run_id=rid, provider=provider)
        return {"run_ids": ids, "question": q}

    @app.get("/api/providers")
    async def providers() -> dict:
        """화면 provider 고르기 — 모델 · effort · 쓸 수 있나(sdk = 키 · CLI = 구독 인증)."""
        d = state["deps"]
        out = []
        for p in PROVIDERS:
            info = describe(d.settings, p)
            if p == "sdk":
                info.update(available=d.anthropic is not None, reason=None if d.anthropic else NO_KEY)
            else:
                a = await auth.check(p, runner.desc_bin(d.settings, p))
                info.update(available=a.ok, reason=None if a.ok else a.detail, auth_kind=a.kind, cli_version=a.version)
            out.append(info)
        judge = {"provider": d.settings.judge_provider, "model": d.settings.judge_model}
        return {"providers": out, "default": d.settings.default_provider, "judge": judge}

    # ───── 일괄 실행(만들되 실행은 사용자 · 코디가) ─────
    def bstore() -> batch.BatchStore:
        return batch.BatchStore(state["deps"].settings.runs_path)

    @app.get("/api/questions")
    async def questions() -> dict:
        try:
            return {"questions": batch.load_questions(state["deps"].settings)}
        except batch.BatchConfigError as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/batches")
    async def batches() -> dict:
        return {"batches": bstore().list(), "config_missing": batch.config_problems(state["deps"].settings)}

    @app.post("/api/batches")
    async def batch_plan(body: BatchIn) -> dict:
        try:
            plan = batch.make_plan(batch.load_questions(state["deps"].settings), body.providers, body.questions)
        except batch.BatchConfigError as e:
            raise HTTPException(409, str(e)) from e
        bstore().save(plan)
        return batch.summary(plan)

    @app.get("/api/batches/{batch_id}")
    async def batch_get(batch_id: str) -> dict:
        try:
            plan = bstore().load(batch_id)
        except FileNotFoundError as e:
            raise HTTPException(404, "배치 없음") from e
        return {**batch.summary(plan), "items": plan["items"], "log": plan.get("log", [])[-20:]}

    @app.post("/api/batches/{batch_id}/start")
    async def batch_start(batch_id: str) -> dict:
        s = state["deps"].settings
        bad = batch.config_problems(s)
        if bad:
            raise HTTPException(409, "일괄 실행 설정을 먼저 정하세요: " + " · ".join(bad))
        if state.get("batch_task") and not state["batch_task"].done():
            raise HTTPException(409, "다른 배치가 도는 중")
        plan = bstore().load(batch_id)
        state["batch_stop"] = False

        async def execute(q: str, provider: str, extra: dict) -> dict:
            rs = await start_run(q, extra, provider=provider)
            rec = await rs.task
            if rec is None:
                raise RuntimeError("판 실행 실패")
            return rec

        state["batch_task"] = asyncio.create_task(batch.run_batch(
            plan, settings=s, bstore=bstore(), execute=execute, run_store=state["store"],
            should_stop=lambda: state.get("batch_stop", False)))
        return {"started": batch_id}

    @app.post("/api/batches/{batch_id}/stop")
    async def batch_stop(batch_id: str) -> dict:
        state["batch_stop"] = True  # 지금 판이 끝나면 멈춤(이어 가기 가능)
        return {"stopping": batch_id}

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


def main() -> None:
    s = get_settings()
    uvicorn.run(create_app(), host="127.0.0.1", port=s.clinic_chat_port, log_level="info")


if __name__ == "__main__":
    main()
