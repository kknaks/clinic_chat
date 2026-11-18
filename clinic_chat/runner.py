"""질문 하나 × 고른 열 → `runs.jsonl` 한 줄(SPEC-002 §7 · WORK-002 §7).

세 열을 동시에 돌리지 않고 **차례로** · 순서는 질문마다 섞고 seed 를 `order` 에 남긴다 · 한 열이 오류여도 나머지를 돈다.

    uv run python -m clinic_chat.runner --question "<질문>" [--columns a,b,c] [--no-b-on]
    uv run python -m clinic_chat.runner --set-id Q08
    uv run python -m clinic_chat.runner --question "<질문>" --columns b,c,b_full   # b_full = 「B 전체」(종류 14 전부 · 채점 대상 아님)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from clinic_chat import progress, prompts
from clinic_chat.columns import col_a, col_b, col_c
from clinic_chat.columns.base import Deps
from clinic_chat.config import Settings, load_settings
from clinic_chat.raw_ledger.raw_tools import RawStore, RawToolError
from clinic_chat.runs import RunStore, app_commit, git_commit, now_iso
from clinic_chat.walk.kinds import KIND_CODES

COLS = ("a", "b", "c")  # 기본 세 열(정확도 탭 · 채점)
ALL_COLS = (*COLS, "b_full")  # `--columns` 가 받는 이름 — b_full = 「B 전체」(단계 ① 같고 읽는 종류만 전부 · O-26 · B-2)


def first_text_tap(clicked_at: float) -> progress.Sink:
    """열마다 첫 `delta` 시각을 재고 그 열 `column_done` 의 결과 `timing` 에 얹는다.

    `click_to_first_text_ms` = 첫 글자(도구 사이 메모 포함) · `click_to_first_answer_ms` = 첫 **답** 글자 —
    `interim`(도구 호출 — 그때까지 글은 메모) 이 오면 다음 `delta` 에서 다시 잰다. 도구가 없으면(B 계열) 두 값 같다."""
    firsts: dict[str, float] = {}
    answers: dict[str, float | None] = {}

    def tap(ev: dict) -> None:
        col = ev.get("column")
        kind = ev.get("event")
        if kind == "delta":
            now = round((time.perf_counter() - clicked_at) * 1000, 1)
            firsts.setdefault(col, now)
            if answers.get(col) is None:
                answers[col] = now
        elif kind == "interim":
            answers[col] = None
        elif kind == "column_done" and isinstance(ev.get("result"), dict):
            t = ev["result"].setdefault("timing", {})
            t["click_to_first_text_ms"] = firsts.get(col)
            t["click_to_first_answer_ms"] = answers.get(col)

    return tap


def raw_manifest(s: Settings) -> dict | None:
    if not s.raw_dir:
        return None
    try:
        r = RawStore(s.raw_dir).raw_list(depth=0)["raw"]
        return {"dir_manifest_sha16": r["dir_manifest_sha16"], "files": r["files"]}
    except RawToolError:
        return None


def hub_commit(s: Settings) -> str | None:
    return s.hub_commit or git_commit(s.hub_worktree)


def answer_key_head(s: Settings, set_id: str | None) -> dict | None:
    if not set_id:
        return None
    from clinic_chat.evalset.answer_key import key_head

    return key_head(s, set_id)


async def run_question(question: str, d: Deps, *, columns: tuple[str, ...] = COLS, set_id: str | None = None,
                       batch_id: str | None = None, b_on: bool = True, seed: int | None = None,
                       store: RunStore | None = None, run_id: str | None = None, effort: str | None = None,
                       concurrent: bool = False, deps_for: Callable[[str], Deps] | None = None,
                       clicked_at: float | None = None) -> dict:
    """`run_id` 를 주면 그 값으로 줄을 쓴다(화면 서버가 SSE 주소를 먼저 알려 주려고 — 없으면 새로 만든다).

    `effort` 를 주면 모든 열(B · B 전체 · C · A · B 켬)의 `--effort` 를 그 값으로(속도 탭) · 없으면 설정 그대로.
    열마다 실제로 넘긴 값은 줄의 `efforts{열: 값}` 에 남는다.

    `concurrent=True` = 고른 열을 동시에 시작(화면만 — CLI runner · batch 는 기본 차례로 · 순서 섞음).
    `deps_for(열)` = 동시 실행 때 열마다 따로 쓸 Deps(허브 · Jev 호출 수가 섞이지 않게) · 없으면 `d`.
    `clicked_at`(`time.perf_counter()` — 화면 요청을 받은 순간)을 주면 열마다 첫 글자까지 ms 를
    `timing.click_to_first_text_ms` 에 적는다(그 열 `column_done` 알림 때 결과에 얹는다).
    """
    s = d.settings
    seed = seed if seed is not None else random.SystemRandom().randrange(2**31)
    order = list(columns)
    if not concurrent:  # 동시 실행은 순서가 없다(seed 는 그대로 남긴다)
        random.Random(seed).shuffle(order)
    cols: dict[str, dict | None] = {}
    models = {"answer": None, "judge": None, "jev": None}  # judge 실제 id 는 채점 때 quality.jsonl 에
    builds: dict[str, dict] = {}
    observed: list[str] = []
    efforts: dict[str, str | None] = {}

    async def one(c: str) -> None:
        dc = deps_for(c) if (concurrent and deps_for) else d
        col_effort = d.spec(s.answer_reasoning if c in ("b", "b_full") else "on", effort).effort_arg()
        efforts[c] = col_effort
        model = None
        with progress.column(c):
            progress.emit("start", effort=col_effort)
            try:
                if c == "a":
                    out = await col_a.run(question, dc, effort)
                elif c == "c":
                    out = await col_c.run(question, dc, effort)
                elif c == "b_full":
                    out = await col_b.run(question, dc, b_on=False, effort=effort, read_kinds=list(KIND_CODES))
                    if out.extra.get("build"):
                        builds[c] = out.extra["build"]
                else:
                    out = await col_b.run(question, dc, b_on=b_on, effort=effort)
                    cols["b_on"] = out.extra.get("b_on")
                    if cols["b_on"] is not None:
                        efforts["b_on"] = d.spec("on", effort).effort_arg()
                    if out.extra.get("build"):
                        builds[c] = out.extra["build"]
                    models["jev"] = ",".join(out.extra.get("jev_models") or []) or None
                cols[c] = out.result
                model = out.observed_model
                if out.observed_model:
                    observed.append(out.observed_model)
            except Exception as e:  # noqa: BLE001 — 한 열 오류여도 나머지를 돈다
                from clinic_chat.columns.base import column

                cols[c] = column(error={"kind": type(e).__name__, "stage": c, "detail": str(e)[:300]})
            progress.emit("column_done", result=cols[c], model=model, effort=col_effort)

    with progress.tap(first_text_tap(clicked_at)) if clicked_at is not None else contextlib.nullcontext():
        if concurrent:
            await asyncio.gather(*(one(c) for c in columns))
        else:
            for c in order:
                await one(c)
    build = builds.get("b") or builds.get("b_full")
    if "b" not in order:
        cols["b_on"] = None
    if build is None:
        try:
            build = (await d.hub.health()).get("build")
        except Exception:  # noqa: BLE001
            build = None
    models["answer"] = ",".join(sorted(set(observed))) or None
    row = {
        "run_id": run_id or uuid.uuid4().hex[:12], "asked_at": now_iso(), "batch_id": batch_id, "set_id": set_id,
        "question": question, "build": {"gold_hash": (build or {}).get("gold_hash"), "aux_hash": (build or {}).get("aux_hash")},
        "commits": {"app": app_commit(), "hub": hub_commit(s)}, "models": models, "raw": raw_manifest(s),
        "answer_key": answer_key_head(s, set_id), "prompts": prompts.head_hashes(d.prompt_base),
        "settings": s.walk_settings(), "order": {"columns": order, "seed": seed},
        "run_mode": "concurrent" if concurrent else "sequential", "efforts": efforts, "effort_override": effort,
        "columns": {k: cols.get(k) for k in ("a", "b", "b_on", "c", *(["b_full"] if "b_full" in order else []))},
    }
    if store is not None:
        store.append_run(row)
    progress.emit("run_done", column=None, run_id=row["run_id"])
    return row


def summary_lines(row: dict, settings: Settings) -> list[str]:
    out = [f"run_id={row['run_id']} set_id={row['set_id']} gold_hash={(row['build'] or {}).get('gold_hash', '')[:12]} "
           f"order={','.join(row['order']['columns'])}"]
    for k in ("a", "b", "b_on", "c", "b_full"):
        c = row["columns"].get(k)
        if not c:
            continue
        calls = c.get("calls") or {}
        out.append(f"  [{k}] end={c.get('end_state')} total_ms={(c.get('timing') or {}).get('total_ms')} "
                   f"llm={calls.get('llm_round_trips')} tool={calls.get('tool_calls')} hub={calls.get('hub_calls')} "
                   f"jev={calls.get('jev_calls')} cited={len(c.get('cited') or [])} "
                   f"answer_chars={len(c.get('answer') or '')} error={'있음' if c.get('error') else '없음'}")
    st = prompts.status(settings)
    if not st["confirmed"]:
        out.append(st["mark"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.runner")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--question")
    g.add_argument("--set-id")
    ap.add_argument("--columns", default="a,b,c")
    ap.add_argument("--no-b-on", action="store_true")
    ap.add_argument("--show-answers", action="store_true", help="터미널에 답 원문도 찍는다(로컬)")
    a = ap.parse_args()
    s = load_settings()
    cols = tuple(x for x in a.columns.split(",") if x)
    bad = [x for x in cols if x not in ALL_COLS]
    if bad or not cols:  # N-10 — 모르는 열 이름(대문자 · b_on · 오타)을 말없이 버리지 않는다
        ap.error(f"--columns 는 {','.join(ALL_COLS)} 중에서(쉼표 · 소문자) — 받은 것 {a.columns!r}")
    q, sid = a.question, None
    if a.set_id:
        from clinic_chat.evalset.loader import load_set

        item = load_set(s).get(a.set_id)
        if item is None:
            raise SystemExit(f"세트에 없는 id: {a.set_id}")
        if item.get("status") == "draft":
            raise SystemExit(f"{a.set_id} 는 draft(사용자 확정 전) — Jev · LLM 으로 보내지 않는다")
        q, sid = item["question"], a.set_id
    d = Deps.from_settings(s)
    row = asyncio.run(run_question(q, d, columns=cols, set_id=sid, b_on=not a.no_b_on,
                                   store=RunStore(Path(s.path(s.runs_dir)))))
    for ln in summary_lines(row, s):
        print(ln)
    if a.show_answers:
        for k, c in row["columns"].items():
            if c:
                print(f"--- {k}\n{c.get('answer')}")
    if sid:
        from clinic_chat.evalset.loader import load_set

        print(f"  expected_end_state={load_set(s)[sid].get('expected_end_state')}")
    print(json.dumps({"runs": str(Path(s.path(s.runs_dir)) / 'runs.jsonl')}, ensure_ascii=False))


if __name__ == "__main__":
    main()
