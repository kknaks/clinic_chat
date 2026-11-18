"""턴 종류 가르기(SPEC-006 §2 TK1 ~ TK7 · TW6 · TT6 · WORK-014 §7 · 코디 W-3).

- 종류 셋 = `new` · `follow` · `talk`. 첫 턴(앞 성공 턴 없음)은 요청 없이 `new`(TK2).
- 0.4(SPEC-008 VF6 · VF7 · VF11): 가르는 주체 = 이해 호출의 `kind` 하나(Jev Noul · 판정 순서 없음 — `verify.py`).
  이 모듈은 앞 턴 읽기(`prev_turn` · `talk_streak` · `prev_state` · `prev_block`)와 앞 대상(`follow_target`)만 남긴다.
- Jev state(TK4) = `question` · `prev{question, answer, targets[이름], ask{slot, options[이름]}, closed_by, empty[]}` · `consult`.
  코드 · 줄 번호를 싣지 않는다.
"""

from __future__ import annotations

from clinic_chat.consult import state as st

KINDS = ("new", "follow", "talk")
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
