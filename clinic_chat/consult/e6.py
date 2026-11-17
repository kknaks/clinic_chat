"""E6-1 ~ E6-5 — 서버 없이 실물 한 턴 흐름을 대화마다(허브 18105 읽기 · Jev · 구독 CLI) · 줄기 이벤트는 메모리로
(SPEC-006 §11-2 · §11-3 · WORK-014 §14). 0.2 의 `e5.py` 꼴.

입력 글은 `e6_inputs.json`(시험 자료 — 화면에 보일 문장이 아니다) · 대화마다 **새 대화로 같은 글을 차례로**. **켠 상태로만 돈다**
(`CHAT_STATE=on` 을 프로세스 env 로). 답의 옳고 그름은 기준이 아니다 — 수와 배관만. 장부는 `CHAT_DIR/e6/turns.jsonl`(gitignore).
허브 판은 응답에 `descended` 칸이 있는지로 가른다(`hub_v07`).

    CHAT_STATE=on uv run python -m clinic_chat.consult.probe --e6 [--only c1_e3f90,sp_cheek]
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from clinic_chat.columns.base import Deps
from clinic_chat.config import Settings
from clinic_chat.consult import prompt as prompt_mod
from clinic_chat.consult.ledger import Ledger
from clinic_chat.consult.render import Render
from clinic_chat.consult.turn import Ctx, run_turn
from clinic_chat.llm.chat_cli import ChatCli, ChatCliSpec

INPUTS = Path(__file__).with_name("e6_inputs.json")
QUESTION_END = re.compile(r"[?？]\s*$")


def inputs() -> list[dict]:
    return json.loads(INPUTS.read_text(encoding="utf-8"))["conversations"]


def _hub_tools(log: list[dict]) -> dict[str, int]:
    return dict(Counter(str(x.get("tool") or x.get("path") or "?") for x in log))


def summary(row: dict, events: list[tuple[str, str]], hub_log: list[dict], hub_v07: bool | None) -> dict:
    cs = row.get("consult") or {}
    tk = cs.get("turn_kind") or {}
    kinds = [k for k, _ in events]
    lst = row.get("list") or {}
    concerns = lst.get("concerns") or []
    sites = row.get("sites") or {}
    close = cs.get("close") or {}
    voice = cs.get("voice") or {}
    answer = row.get("answer") or ""
    material = row.get("material") or {}
    return {
        "turn": row["turn"], "question": row.get("question"), "hub_v07": hub_v07,
        "turn_kind": {k: tk.get(k) for k in ("kind", "nouls", "rule", "requests", "ms", "error")},
        "walks": tk.get("walks") or [], "follow_target": cs.get("follow_target"),
        "end_state": row.get("end_state"), "error": row.get("error"),
        "targets": [(x.get("table"), x.get("code"), x.get("by")) for x in row.get("targets") or []],
        "jev_requests": cs.get("jev_requests"), "hub_requests": _hub_tools(hub_log), "hub_n": len(hub_log),
        "queries": [(q.get("rule"), q.get("tool"), q.get("total"), q.get("skipped")) for q in cs.get("queries") or []],
        "close": {"by": close.get("by"), "said": close.get("said"), "alt": [a.get("name") for a in close.get("alt") or []],
                  "offered": close.get("offered")},
        "ask": cs.get("ask"), "ask_why": cs.get("ask_why"), "empty": cs.get("empty"),
        "voice": {"n": voice.get("n"), "hits": voice.get("hits")},
        "ends_with_question": bool(QUESTION_END.search(answer.strip())),
        "focus": (cs.get("after") or {}).get("focus"),
        "concerns": [(x.get("code"), x.get("state"), (x.get("source") or {}).get("how"))
                     for x in (cs.get("after") or {}).get("concerns") or []],
        "list": [{"concern": c.get("concern_code"), "procedures": (c.get("counts") or {}).get("procedures"),
                  "descended": [(m.get("modality_code"), len(m.get("procedures") or [])) for m in c.get("descended") or []],
                  "hidden": c.get("hidden"), "site": c.get("site")} for c in concerns],
        "sites": {"dots": [(d.get("site_code"), d.get("concern_codes"), d.get("focus")) for d in sites.get("dots") or []],
                  "labels": [(d.get("site_code"), d.get("concern_codes")) for d in sites.get("labels") or []]}
        if row.get("sites") is not None else None,
        "events": [k for i, k in enumerate(kinds) if k not in ("delta", "step") or kinds.index(k) == i],
        "material_new_lines": len(material.get("new_lines") or []),
        "input_bytes": ((row.get("trace") or {}).get("llm_input_bytes")
                        or len((row.get("llm_user_message") or "").encode("utf-8"))),
        "timing": {k: (row.get("timing") or {}).get(k) for k in ("first_list_ms", "ttft_ms", "total_ms")},
        "answer_chars": len(answer), "answer": answer,
    }


def aggregate(convs: list[dict]) -> dict:
    """E6-2 ~ E6-5 — 대화마다 첫 턴은 가르기 밖(TK2)."""
    later = [t for c in convs for t in c["turns"][1:]]
    every = [t for c in convs for t in c["turns"]]
    talk = [t for t in every if t["turn_kind"]["kind"] == "talk"]
    asks = [t for t in every if t["close"]["by"] == "ask"]
    return {
        "e6_2": {"n": len(later), "kinds": dict(Counter(t["turn_kind"]["kind"] for t in later)),
                 "nouls": [(t["question"], t["turn_kind"]["nouls"], t["turn_kind"]["kind"]) for t in later],
                 "ms": [t["turn_kind"]["ms"] for t in later], "errors": sum(1 for t in later if t["turn_kind"]["error"])},
        "e6_3": [{"question": t["question"], "walks": t["walks"], "hub_requests": t["hub_requests"],
                  "jev_requests": t["jev_requests"], "queries": t["queries"]} for t in talk],
        "e6_4": {"close_line_written": sum(1 for t in every if t["close"]["said"] is not None), "turns": len(every),
                 "ask_said_zero": sum(1 for t in asks if t["close"]["said"] == 0)},
        "e6_5": {"voice_n": sum(t["voice"]["n"] or 0 for t in every),
                 "hits": [h for t in every for h in t["voice"]["hits"] or []],
                 "null_close_ends_with_question": sum(1 for t in every if t["close"]["by"] is None
                                                      and t["ends_with_question"])},
    }


async def run_e6(s: Settings, only: list[str] | None = None) -> dict:
    if s.chat_state != "on":
        return {"ran": False, "why": "chat_state_off"}
    holder: dict = {}

    def deps(st: Settings) -> Deps:
        d = Deps.from_settings(st)
        holder["d"] = d
        return d
    ctx = Ctx(s, Ledger(s.chat_dir_path() / "e6"), Render(), ChatCli(ChatCliSpec.from_settings(s)), deps)
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
            d = holder.get("d")
            log = list(d.hub.log) if d is not None else []
            v07 = None
            for m in conv.memo.values():
                for r in m or []:
                    if r.call.tool == "concern_candidates":
                        v07 = "descended" in r.ra.as_response()
            turns.append(summary(conv.turns[-1], events, log, v07))
        out.append({"id": c["id"], "conv_id": conv.conv_id, "turns": turns})
    return {"ran": True, "conversations": out, "aggregate": aggregate(out)}
