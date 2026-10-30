"""한 질문 실행 — 세 케이스를 같은 `t0` 에서 `gather` 로 동시 시작 · 한 열 오류여도 나머지 계속(SPEC-006 §2-1 · §4).

셋 다 끝나면 ① 단어 검사 → ② Opus 채점(§5) → 기록 줄 dict. 이벤트는 SSE 계약(§4-3) 이름 그대로 emit.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Callable
from typing import Any

from clinic_chat import prompts
from clinic_chat.cases import case1, case2, case3
from clinic_chat.clients.anthropic_client import ANSWER_EFFORT, ANSWER_THINKING
from clinic_chat.deps import Deps
from clinic_chat.llm import CLI_PROVIDERS, PROVIDERS, auth
from clinic_chat.llm.specs import describe
from clinic_chat.pricing import PRICING_AS_OF
from clinic_chat.runs import now_iso
from clinic_chat.timing import CaseTimer, Clock

CASES = ("case1", "case2", "case3")
Emit = Callable[[str, dict], None]


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


async def execute(deps: Deps, question: str, emit: Emit, *, run_id: str | None = None, quality: bool | None = None,
                  jev_seed: int | None = None, judge_seed: int | None = None, provider: str | None = None) -> dict:
    """provider = sdk · claude-cli · codex-cli(질문마다 · 세 케이스 모두 같은 provider).
    quality=None 이면 CLI 판은 자동 채점 · sdk 판은 설정 AUTO_QUALITY(기본 끔 — 「채점」 버튼)."""
    provider = provider or deps.settings.default_provider
    if provider not in PROVIDERS:
        raise ValueError(f"모르는 provider: {provider}")
    if quality is None:
        quality = auto_quality(deps.settings, provider)
    desc = describe(deps.settings, provider)
    auth_info = None
    if provider in CLI_PROVIDERS:  # 실행 앞 구독 확인 — 아니면 세 열 오류(조용히 API 과금 금지)
        a = await auth.check(provider, desc_bin(deps.settings, provider))
        auth_info = {"kind": a.kind, "detail": a.detail, "cli_version": a.version}
        emit("provider", {**desc, "auth": auth_info})
        if not a.ok:
            quality = False
    else:
        emit("provider", desc)
    run_id = run_id or new_run_id()
    jev_seed = random.randrange(2**31) if jev_seed is None else jev_seed
    judge_seed = random.randrange(2**31) if judge_seed is None else judge_seed
    asked_at = now_iso()
    clock = Clock(now=deps.now)
    clock.start()  # t0 — 세 케이스 같은 값

    timers = {c: CaseTimer(clock, emit_stage=lambda st, c=c: emit("stage", {"case": c, **st})) for c in CASES}
    texts = {c: (lambda t, c=c: emit("text", {"case": c, "delta": t})) for c in CASES}
    runners = {
        "case1": lambda: case1.run(deps, question, timers["case1"], texts["case1"], provider=provider),
        "case2": lambda: case2.run(deps, question, timers["case2"], texts["case2"], provider=provider),
        "case3": lambda: case3.run(deps, question, timers["case3"], texts["case3"], seed=jev_seed, provider=provider),
    }
    if auth_info is not None and auth_info["kind"] != "subscription":
        async def refused() -> dict:
            return {"answer": "", "stop_reason": None, "loop_capped": False, "refusal": False,
                    "error": f"인증: {auth_info['detail']}", "timing": {}, "tokens": {}, "trace": {}}
        runners = {c: refused for c in CASES}

    async def one(c: str) -> dict:
        try:
            r = await runners[c]()
        except Exception as e:  # noqa: BLE001 — 그 열만 오류
            t = timers[c]
            t.finish()
            r = {"answer": "", "stop_reason": None, "loop_capped": False, "refusal": False,
                 "error": f"{type(e).__name__}: {e}"[:500], "timing": t.as_dict(), "tokens": {}, "trace": {}}
        if not r.get("timing"):
            timers[c].finish()
            r["timing"] = timers[c].as_dict()
        r["timing"]["t0"] = 0.0
        if r.get("error"):
            emit("error", {"case": c, "message": r["error"]})
        emit("trace", {"case": c, **r.get("trace", {})})
        r.setdefault("cost", {"usd": 0.0, "by_stage": {}})
        emit("case_done", {"case": c, "timing": r["timing"], "tokens": r.get("tokens", {}), "error": r.get("error"),
                           "stop_reason": r.get("stop_reason"), "loop_capped": r.get("loop_capped"),
                           "refusal": r.get("refusal"), "cost": r["cost"]})
        return r

    results = await asyncio.gather(*(one(c) for c in CASES))
    cases: dict[str, Any] = dict(zip(CASES, results))
    for r in cases.values():
        r.setdefault("word_check", {})
        r.setdefault("judge", {})

    judge_order: list[str] = []
    quality_cost = None
    if quality:
        q = await run_quality(deps, question, cases, emit, judge_seed)
        judge_order, quality_cost = q["judge_order"], q["cost_usd"]

    s = deps.settings
    gold_hash = cases["case2"]["trace"].get("gold_build_hash") or cases["case3"]["trace"].get("gold_build_hash")
    cli_any = next((cases[c]["trace"].get("cli") for c in CASES if cases[c].get("trace", {}).get("cli")), None) or {}
    record = {
        "run_id": run_id, "asked_at": asked_at, "question": question, "provider": provider,
        "versions": {"answer_provider": provider, "answer_model": desc["model"], "answer_effort": desc["effort"],
                     "answer_model_observed": cli_any.get("observed_model"),
                     "judge_provider": s.judge_provider, "judge_model": s.judge_model, "extract_model": s.extract_model,
                     "cli_version": (auth_info or {}).get("cli_version"), "auth_kind": (auth_info or {}).get("kind")
                     or ("api" if provider == "sdk" else None),
                     "jev_model": s.typesafe_default_model, "prompts": prompts.versions(),
                     "gold_build_hash": gold_hash,
                     "raw_manifest_sha16": cases["case1"]["trace"].get("raw_manifest_sha16")
                     or (deps.raw_store.dir_manifest_sha16 if deps.raw_store else None),
                     "app_commit": deps.app_commit},
        "settings": {"thinking": ANSWER_THINKING["type"] if provider == "sdk" else "cli-default",
                     "effort": ANSWER_EFFORT if provider == "sdk" else desc["effort"],
                     "tool_loop_max": s.case_tool_loop_max if provider == "sdk" else None,
                     "max_budget_usd": s.cli_max_budget_usd if provider == "claude-cli" else None,
                     "service_tier": desc.get("service_tier"), "timeout_sec": s.cli_timeout_sec if provider != "sdk" else None,
                     # 도구 · 허브 결과 입력 상한(세 케이스 공통 하나) — CLI 판은 case1 · 2 에 못 넣어 셋 다 안 넣음
                     "input_cap_bytes": s.tool_result_max_bytes if provider == "sdk" else None,
                     "jev_confidence_min": s.jev_confidence_min, "jev_option_order_seed": jev_seed},
        "cases": cases, "judge_order": judge_order, "judge_seed": judge_seed,
        "cost": run_cost(cases, quality_cost),
    }
    emit("cost", record["cost"])
    return record  # run_done 은 서버가 기록을 쓴 뒤 보냄


def auto_quality(s, provider: str) -> bool:
    """CLI 판은 자동 채점 · sdk 판은 설정 AUTO_QUALITY(기본 끔 — 「채점」 버튼)."""
    return True if provider in CLI_PROVIDERS else s.auto_quality


def desc_bin(s, provider: str) -> str:
    return s.claude_cli_bin if provider == "claude-cli" else s.codex_cli_bin


def run_cost(cases: dict, quality_usd: float | None) -> dict:
    """질문당 예상 $ — 열마다(Sonnet · Jev) · 답 합계 · 채점(①②) · 전체. 단가표 날짜를 같이 남김."""
    by = {c: round((cases[c].get("cost") or {}).get("usd") or 0.0, 6) for c in CASES}
    answers = round(sum(by.values()), 6)
    billed = sorted({(cases[c].get("cost") or {}).get("billed") or "api" for c in CASES})
    return {"cases": by, "answers_usd": answers, "quality_usd": quality_usd, "billed": billed,
            "total_usd": round(answers + (quality_usd or 0.0), 6), "pricing_as_of": PRICING_AS_OF}


async def run_quality(deps: Deps, question: str, cases: dict, emit: Emit, judge_seed: int) -> dict:
    """① → ② (셋 다 끝난 뒤 · README §5). → {judge_order, cost_usd, usage}. 기록 칸 word_check · judge 를 채운다."""
    from clinic_chat.quality import judge, word_check

    for c in CASES:
        r = cases[c]
        if r.get("error") or not r.get("answer"):
            continue
        try:
            r["word_check"] = await word_check.check_answer(deps, r["answer"])
        except Exception as e:  # noqa: BLE001 — ① 실패는 그 열 표시만
            r["word_check"] = {"error": f"{type(e).__name__}: {e}"[:300]}
        emit("word_check", {"case": c, **r["word_check"]})
    q_costs = [(cases[c].get("word_check") or {}).get("cost_usd") or 0.0 for c in CASES]
    order = judge.blind_order([c for c in CASES if cases[c].get("answer") and not cases[c].get("error")], judge_seed)
    if not order:
        return {"judge_order": [], "cost_usd": round(sum(q_costs), 6)}
    try:
        jr = await judge.judge(deps, question, {c: cases[c]["answer"] for c in order}, order)
    except Exception as e:  # noqa: BLE001 — ② 실패는 표시만
        jr = {"error": f"{type(e).__name__}: {e}"[:300], "per_case": {}}
    for c in order:
        cases[c]["judge"] = jr.get("per_case", {}).get(c, {"error": jr.get("error")})
    emit("judge", {"cases": {c: cases[c]["judge"] for c in order}, "order": order, "error": jr.get("error"),
                   "cost_usd": jr.get("cost_usd"), "usage": jr.get("usage")})
    total = round(sum(q_costs) + (jr.get("cost_usd") or 0.0), 6)
    emit("quality_cost", {"cost_usd": total})
    return {"judge_order": order, "cost_usd": total, "judge_usage": jr.get("usage"), "judge_cost_usd": jr.get("cost_usd")}
