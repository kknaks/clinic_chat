"""세트 실행기(WORK-002 §10-1) — 세트 파일 → 문항마다 runner → `runs.jsonl`.

- 시작 때 한 번 확인(허브 health + gold_hash · Jev 키 「있음」 · CLI 구독 · RAW_DIR · 세트 파일 · 답 키 confirmed 수).
  하나라도 안 되면 돌리지 않고 무엇이 빠졌는지 말한다. 「프롬프트 미확정」 은 찍기만 한다(막지 않는다).
- `status: draft` 문항은 돌리지 않는다(사용자 확정 전 질문을 Jev · LLM 으로 보내지 않는다 — 건너뛴 수를 말한다).
- 문항 사이 쉼(`BATCH_PAUSE_SEC` — 구독 한도) · `--resume <batch_id>` 는 이미 쓴 `set_id` 를 건너뛴다.

    uv run python -m clinic_chat.batch --check
    uv run python -m clinic_chat.batch --label <라벨> [--ids Q01,Q10] [--bundle <묶음>] [--range <범위>] [--columns a,c]
    uv run python -m clinic_chat.batch --label speed --ids Q01,Q22 --columns b,c,b_full   # b_full = B 전체(채점 대상 아님)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path

from clinic_chat import prompts
from clinic_chat.columns.base import Deps
from clinic_chat.config import Settings, load_settings
from clinic_chat.evalset.answer_key import key_head
from clinic_chat.evalset.loader import load_set
from clinic_chat.llm import auth
from clinic_chat.raw_ledger.raw_tools import RawStore, RawToolError
from clinic_chat.runner import ALL_COLS, COLS, run_question
from clinic_chat.runs import RunStore


async def check(s: Settings, d: Deps) -> dict:
    out: dict[str, dict] = {}
    try:
        h = await d.hub.health()
        out["hub"] = {"ok": True, "gold_hash": (h.get("build") or {}).get("gold_hash")}
    except Exception as e:  # noqa: BLE001
        out["hub"] = {"ok": False, "detail": f"허브 안 됨: {str(e)[:120]}"}
    out["jev_key"] = {"ok": s.has_typesafe_key, "detail": "있음" if s.has_typesafe_key else "없음"}
    a = await auth.check(s.claude_cli_bin)
    out["cli"] = {"ok": a.ok, "detail": a.detail}
    try:
        n = len(RawStore(s.raw_dir or "").files)
        out["raw"] = {"ok": True, "files": n}
    except RawToolError as e:
        out["raw"] = {"ok": False, "detail": str(e)}
    items = load_set(s)
    drafts = sum(1 for x in items.values() if x.get("status") == "draft")
    out["set"] = {"ok": bool(items), "items": len(items), "draft": drafts}
    heads = {qid: key_head(s, qid) for qid in items}
    conf = sum(1 for h in heads.values() if h and h["confirmed"])
    have = sum(1 for h in heads.values() if h)
    out["answer_key"] = {"ok": True, "keys": have, "confirmed": conf, "unconfirmed": have - conf,
                         "note": "채점은 confirmed 키 문항만 돈다" if have - conf else ""}
    st = prompts.status(s)
    out["prompts"] = {"ok": True, "confirmed": st["confirmed"], "mark": st["mark"]}
    out["ready"] = {"ok": all(v.get("ok") for k, v in out.items() if k in ("hub", "jev_key", "cli", "raw", "set"))}
    return out


def select(items: dict[str, dict], *, ids: list[str] | None, bundle: str | None, range_: str | None) -> list[dict]:
    out = []
    for it in items.values():
        if ids and it["id"] not in ids:
            continue
        if bundle and it.get("source") != bundle and it.get("bundle") != bundle:
            continue
        if range_ and it.get("range") != range_:
            continue
        out.append(it)
    return out


async def run_batch(s: Settings, d: Deps, *, label: str, ids=None, bundle=None, range_=None,
                    columns: tuple[str, ...] = COLS, b_on: bool = True, resume: str | None = None,
                    store: RunStore | None = None, skip_check: bool = False, log=print) -> dict:
    store = store or RunStore(Path(s.path(s.runs_dir)))
    if not skip_check:
        c = await check(s, d)
        if not c["ready"]["ok"]:
            missing = [k for k, v in c.items() if k in ("hub", "jev_key", "cli", "raw", "set") and not v.get("ok")]
            return {"ok": False, "missing": missing, "check": c}
    items = select(load_set(s), ids=ids, bundle=bundle, range_=range_)
    drafts = [x["id"] for x in items if x.get("status") == "draft"]
    todo = [x for x in items if x.get("status") != "draft"]
    batch_id = resume or f"{datetime.now():%Y%m%d-%H%M%S}-{label}-{uuid.uuid4().hex[:4]}"
    done = {r.get("set_id") for r in store.runs() if r.get("batch_id") == batch_id} if resume else set()
    log(f"batch_id={batch_id} 문항 {len(todo)} · draft 건너뜀 {len(drafts)} · 이어 돌리기 건너뜀 {len(done & {x['id'] for x in todo})}")
    models: set[str] = set()
    n = 0
    for i, it in enumerate(todo):
        if it["id"] in done:
            continue
        if i and s.batch_pause_sec:
            await asyncio.sleep(s.batch_pause_sec)
        row = await run_question(it["question"], d, columns=columns, set_id=it["id"], batch_id=batch_id, b_on=b_on,
                                 store=store)
        n += 1
        if row["models"].get("answer"):
            models.add(row["models"]["answer"])
        ms = {k: (v or {}).get("timing", {}).get("total_ms") for k, v in row["columns"].items() if v}
        log(f"  {it['id']} {json.dumps(ms)}")
    st = prompts.status(s)
    summary = {"ok": True, "batch_id": batch_id, "ran": n, "draft_skipped": len(drafts),
               "model_changed": len(models) > 1, "answer_models": sorted(models),
               "prompts": st["mark"] or "확정"}
    log(json.dumps(summary, ensure_ascii=False))
    if st["mark"]:
        log(st["mark"])
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.batch")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--label", default="run")
    ap.add_argument("--ids")
    ap.add_argument("--bundle")
    ap.add_argument("--range")
    ap.add_argument("--columns", default="a,b,c")
    ap.add_argument("--b-on", dest="b_on", action="store_true", default=True)
    ap.add_argument("--no-b-on", dest="b_on", action="store_false")
    ap.add_argument("--resume")
    a = ap.parse_args()
    s = load_settings()
    d = Deps.from_settings(s)
    if a.check:
        c = asyncio.run(check(s, d))
        for k, v in c.items():
            print(f"{k:11s} {'됨' if v.get('ok') else '안 됨'}  {json.dumps({x: y for x, y in v.items() if x != 'ok'}, ensure_ascii=False)}")
        return
    cols = tuple(x for x in a.columns.split(",") if x)
    bad = [x for x in cols if x not in ALL_COLS]
    if bad or not cols:  # N-10 — 모르는 열 이름(대문자 · b_on · 오타)을 말없이 버리지 않는다
        ap.error(f"--columns 는 {','.join(ALL_COLS)} 중에서(쉼표 · 소문자) — 받은 것 {a.columns!r}")
    r = asyncio.run(run_batch(s, d, label=a.label, ids=a.ids.split(",") if a.ids else None, bundle=a.bundle,
                              range_=a.range, columns=cols, b_on=a.b_on, resume=a.resume))
    if not r["ok"]:
        print(json.dumps({"ok": False, "missing": r["missing"]}, ensure_ascii=False))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
