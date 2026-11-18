"""E5-1 ~ E5-4 — 서버 없이 실물 한 턴 흐름을 대화마다(허브 18105 읽기 · Jev · 구독 CLI) · 줄기 이벤트는 메모리로(WORK-012 §14).

입력 글은 `e5_inputs.json`(시험 자료 — 화면에 보일 문장이 아니다). **켠 상태로만 돈다** — `CHAT_STATE=on` 을 프로세스 env 로
붙여 띄운다(발주서 W-3). 답의 옳고 그름은 기준이 아니다 — 수와 배관만. 장부는 `CHAT_DIR/e5/turns.jsonl`(gitignore 로컬).

    CHAT_STATE=on uv run python -m clinic_chat.consult.probe --e5
"""

from __future__ import annotations

import json
from pathlib import Path

from clinic_chat.config import Settings
from clinic_chat.consult import prompt as prompt_mod
from clinic_chat.consult.ledger import Ledger
from clinic_chat.consult.render import Render
from clinic_chat.consult.turn import Ctx, run_turn
from clinic_chat.llm.chat_cli import ChatCli, ChatCliSpec

INPUTS = Path(__file__).with_name("e5_inputs.json")


def inputs() -> list[dict]:
    return json.loads(INPUTS.read_text(encoding="utf-8"))["conversations"]


def _targets(row: dict) -> list[dict]:
    tf = ((row.get("trace") or {}).get("front") or {})
    return [{"id": t.get("id"), "noul": t.get("noul"), "decision": t.get("decision"),
             **({"option_from": t["option_from"]} if t.get("option_from") is not None else {})}
            for t in tf.get("targets") or []]


def summary(row: dict, start: dict, events: list[tuple[str, str]]) -> dict:
    cs = row.get("consult") or {}
    kinds = [k for k, _ in events]
    lst = row.get("list") or {}
    q = cs.get("queries") or []
    qy1 = [x for x in q if x.get("rule") in ("QY1", "QY5")]
    syn = lst.get("synergy") or []
    breadth = (row.get("trace") or {}).get("breadth") or []
    return {
        "turn": row["turn"], "question": row.get("question"), "question_matches_start": row.get("question") ==
        start.get("question"), "consult_state": start.get("consult_state"),
        "end_state": row.get("end_state"), "error": row.get("error"), "timing": row.get("timing"),
        "diff": [{k: d.get(k) for k in ("op", "slot", "code", "from", "to", "why")} for d in cs.get("diff") or []],
        "queries": [{k: x.get(k) for k in ("rule", "tool", "total", "ms", "skipped")} | {"request": x.get("request")}
                    for x in q],
        "skipped_walk": sum(1 for x in q if x.get("skipped") == "walk"),
        "qy1_site": [x["request"].get("site") for x in qy1], "list_rows": [((c.get("counts") or {}).get("rows"),
                                                                             (c.get("counts") or {}).get("procedures"))
                                                                            for c in lst.get("concerns") or []],
        "jev_requests": cs.get("jev_requests"), "hub_queries_walk": len(breadth),
        "hub_queries_consult": cs.get("hub_queries"),
        "options_carried": cs.get("options_carried"), "ask": cs.get("ask"), "ask_why": cs.get("ask_why"),
        "blanks": cs.get("blanks"), "retract": cs.get("retract"), "retract_ms": cs.get("retract_ms"),
        "retract_resolve_ms": cs.get("retract_resolve_ms"), "retract_error": cs.get("retract_error"),
        "targets": _targets(row), "focus": (cs.get("after") or {}).get("focus"),
        "object": {k: [(x.get("table"), x.get("code") or x.get("kind"), x.get("state"))
                       for x in (cs.get("after") or {}).get(k) or []]
                   for k in ("concerns", "sites", "subjects", "procedures", "wishes")},
        "synergy": [{"a": b["a_code"], "inside": [(p["code"], p["caution"]) for p in b["inside"]],
                     "outside_n": b["outside_n"], "outside_caution": [p["code"] for p in b["outside"] if p["caution"]]}
                    for b in syn],
        "synergy_lines": cs.get("synergy_lines"), "consult_error": cs.get("error"),
        "events": [k for i, k in enumerate(kinds) if k not in ("delta", "step") or kinds.index(k) == i],
        "order_ok": ("list" not in kinds or "delta" not in kinds or kinds.index("list") < kinds.index("delta")),
        "tokens": row.get("tokens"), "answer_chars": len(row.get("answer") or ""),
    }


async def run_e5(s: Settings, only: list[str] | None = None) -> dict:
    if s.chat_state != "on":
        return {"ran": False, "why": "chat_state_off"}
    ctx = Ctx(s, Ledger(s.chat_dir_path() / "e5"), Render(), ChatCli(ChatCliSpec.from_settings(s)))
    out = []
    for c in inputs():
        if only and c["id"] not in only:
            continue
        conv = ctx.ledger.new_conv(prompt_mod.keep(s.chat_prompt_path(), s.chat_dir_path()))
        turns = []
        for q in c["turns"]:
            start = ctx.ledger.start(conv, q, s.chat_state)
            events: list[tuple[str, str]] = []
            await run_turn(ctx, conv, start["turn"], q, lambda k, h, ev=events: ev.append((k, h)))
            turns.append(summary(conv.turns[-1], start, events))
        out.append({"id": c["id"], "e5": c["e5"], "conv_id": conv.conv_id, "turns": turns})
    return {"ran": True, "conversations": out}
