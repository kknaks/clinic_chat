"""턴 종류 가르기(SPEC-006 §2 TK1 ~ TK7 · TW6 · TT6 · WORK-014 §7 · 코디 W-3).

- 종류 셋 = `new` · `follow` · `talk`. 첫 턴(앞 성공 턴 없음)은 요청 없이 `new`(TK2).
- 가르는 주체 = Jev Noul 셋(`tk_new` · `tk_talk` · `tk_follow`) — 거둠 요청에 함께 실어 요청 하나(TK3 · `state.retract`).
- 판정(TK5): ① `tk_new` ≥ yes → `new` ② `tk_talk` ≥ yes → `talk` ③ `tk_follow` ≥ yes → `follow` ④ 그 밖 → `new`.
  요청 오류 · 시간 초과 → `new` + `error`(TK6 — 턴을 죽이지 않는다). 잇단 `talk` 가 `CHAT_TALK_MAX` 면 요청 없이 `new`(TT6).
- Jev state(TK4) = `question` · `prev{question, answer, targets[이름], ask{slot, options[이름]}, closed_by, empty[]}` · `consult`.
  코드 · 줄 번호를 싣지 않는다.
"""

from __future__ import annotations

from clinic_chat.consult import state as st

KINDS = ("new", "follow", "talk")
EXTRA = {"new": "tk_new", "talk": "tk_talk", "follow": "tk_follow"}
OBJ_TABLES = set(st.SLOT_OF)  # 객체 칸에 드는 표 — 그 밖(package · adverse · condition · anatomy · offer)은 CR1 그대로(W-3)


def prev_turn(turns: list[dict]) -> dict | None:
    """바로 앞 **성공** 턴의 맺는 줄."""
    return next((t for t in reversed(turns) if t.get("error") is None and (t.get("llm") or {}).get("answered")), None)


def talk_streak(turns: list[dict]) -> int:
    """끝에서부터 잇단 `talk` 턴 수(성공 턴만 센다 · 오류 턴은 건너뛴다)."""
    k = 0
    for t in reversed(turns):
        if t.get("error") is not None:
            continue
        if ((t.get("consult") or {}).get("turn_kind") or {}).get("kind") != "talk":
            break
        k += 1
    return k


def empty_names(row: dict, kind_labels: dict[str, str]) -> list[dict]:
    return [{"about": (x.get("about") or {}).get("name") or "", "what": kind_labels.get(x.get("what"), "")}
            for x in (row.get("consult") or {}).get("empty") or []]


def prev_state(row: dict, kind_labels: dict[str, str]) -> dict:
    """TK4 — Jev 에 주는 앞 턴(이름만)."""
    c = row.get("consult") or {}
    ask = c.get("ask")
    return {"question": row.get("question") or "", "answer": row.get("answer") or "",
            "targets": [x.get("name") or "" for x in row.get("targets") or []],
            "ask": ({"slot": ask.get("slot"), "options": [o.get("name") or "" for o in ask.get("options") or []]}
                    if ask else None),
            "closed_by": (c.get("close") or {}).get("by"), "empty": empty_names(row, kind_labels)}


def prev_block(row: dict | None, kind_labels: dict[str, str]) -> dict:
    """TT3 — `talk` 턴의 「앱 판정」 앞 턴 블록."""
    row = row or {}
    return {"closed_by": ((row.get("consult") or {}).get("close") or {}).get("by"),
            "empty": empty_names(row, kind_labels), "end_state": row.get("end_state")}


def judge(nouls: dict[str, float] | None, yes_at: float) -> tuple[str, str]:
    """TK5 — (종류, 규칙)."""
    v = nouls or {}
    for i, k in enumerate(("new", "talk", "follow"), 1):
        if (v.get(k) or 0.0) >= yes_at:
            return k, f"TK5-{i}"
    return "new", "TK5-4"


def follow_target(obj: dict, prev: dict | None) -> dict | None:
    """TW6 · W-3 — ① 앞 성공 턴의 대상이 객체 밖 표면 그 대상(0.1 CR1) ② 객체의 가장 최근 `subjects[]`(축) ③ `focus` 고민."""
    for x in (prev or {}).get("targets") or []:
        if x.get("table") and x.get("table") not in OBJ_TABLES and x.get("code"):
            return {"table": x["table"], "code": x["code"], "name": x.get("name") or ""}
    subs = st.axis_items(obj, "subjects")
    if subs:
        best = max(enumerate(subs), key=lambda e: (max([h["turn"] for h in e[1].get("history") or []] or [0]), e[0]))[1]
        return {"table": best["table"], "code": best["code"], "name": best.get("name") or ""}
    f = st.focus_item(obj)
    if f is not None:
        return {"table": "concern", "code": f["code"], "name": f.get("name") or ""}
    return None
