"""E3 — 서버 없이 실물 한 턴 흐름을 세 번(허브 읽기 · Jev · 구독 CLI) · 줄기 이벤트는 메모리로(WORK-011 §15).

세 턴 꼴(조사 §5-1 · §5-2): 고민 → 그중 한 계열 → 다른 계열과 차이. 답의 옳고 그름은 기준이 아니다 — 수와 배관만.
장부는 `CHAT_DIR/e3/turns.jsonl`(gitignore 로컬). `uv run python -m clinic_chat.consult.probe --e3`
"""

from __future__ import annotations

import re

from clinic_chat.config import Settings
from clinic_chat.consult import prompt as prompt_mod
from clinic_chat.consult.ledger import Ledger
from clinic_chat.consult.render import Render
from clinic_chat.consult.turn import Ctx, run_turn
from clinic_chat.llm.chat_cli import ChatCli, ChatCliSpec

E3_TURNS = ("팔자주름이 고민이에요", "그중 필러는 어떤 시술이에요?", "보톡스랑은 뭐가 달라요?")


def _summary(row: dict, events: list[tuple[str, str]], mark: str) -> dict:
    mat = row.get("material") or {}
    nl = mat.get("new_lines") or []
    lst = row.get("list") or {}
    kinds = [k for k, _ in events]
    shown = "".join(h for k, h in events if k in ("delta", "done"))
    llm = row.get("llm") or {}
    return {
        "turn": row["turn"], "end_state": row.get("end_state"), "reason": row.get("reason"),
        "error": row.get("error"), "timing": row.get("timing"),
        "new_lines": len([x for x in nl if not x.get("summary")]), "summary_lines": len([x for x in nl if x.get("summary")]),
        "seen_lines": len(mat.get("seen_lines") or []), "tokens": row.get("tokens"),
        "thinking_seen": row.get("thinking_seen"), "observed_model": llm.get("observed_model"),
        "llm_mode": llm.get("mode"), "session_state": row.get("session_state"),
        "carried": [{"code": x["code"], "noul": x["noul"], "decision": x["decision"]} for x in row.get("carried") or []],
        "targets": [{"table": x["table"], "code": x["code"], "by": x["by"]} for x in row.get("targets") or []],
        "events": [k for i, k in enumerate(kinds) if k != "delta" or kinds.index("delta") == i],
        "order_ok": ("list" not in kinds or "delta" not in kinds or kinds.index("list") < kinds.index("delta")),
        "tail_on_screen": mark in shown or bool(re.search(r"^\s*\d+\s*:\s*\d", shown, re.M)),
        "list_counts": [c.get("counts") for c in lst.get("concerns") or []],
        "list_modalities": [[m["modality_name"] for m in c["modalities"]] for c in lst.get("concerns") or []],
        "hub_reads": [{"tool": b.get("tool"), "total": b.get("total"), "purpose": b.get("purpose")}
                      for b in (row.get("trace") or {}).get("breadth") or []][:12],
        "sites": row.get("sites"), "answer_chars": len(row.get("answer") or ""),
    }


async def run_e3(s: Settings) -> dict:
    ctx = Ctx(s, Ledger(s.chat_dir_path() / "e3"), Render(), ChatCli(ChatCliSpec.from_settings(s)))
    conv = ctx.ledger.new_conv(prompt_mod.keep(s.chat_prompt_path(), s.chat_dir_path()))
    out = []
    for q in E3_TURNS:
        n = ctx.ledger.start(conv, q)["turn"]
        events: list[tuple[str, str]] = []
        await run_turn(ctx, conv, n, q, lambda k, h, ev=events: ev.append((k, h)))
        out.append(_summary(conv.turns[-1], events, conv.prompt.tail_marker))
    return {"conv_id": conv.conv_id, "prompt_version": conv.prompt.version, "turns": out}
