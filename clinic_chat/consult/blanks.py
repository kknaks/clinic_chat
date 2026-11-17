"""빈 칸 · 하나 고르기(SPEC-005 §6-1 BK1 ~ BK5 · §6-2 NQ1 ~ NQ9 · WORK-012 WP-4 · 코디 W-2 · W-4 · 검수 W-1 (가)).

- 빈 칸은 객체에서 **턴마다 센다**(저장하지 않는다 — 장부에는 그 턴의 값). 선택지는 전부 허브 응답 행의 코드다(지어내지 않는다).
  BK1 `interest` · BK2 `concern`(대상의 적응 — QY3) · BK3 `concern_narrow`(하위 고민) · BK4 `site`(하위 고민들의 고민 부위) ·
  BK5 `confirm`(추정 하나) · 부위만 대상(검수 W-1 (가) — `focus` 가 없을 때만) = `concern` + `about` 이 부위(QY7 의 고민).
- 코디 W-4: BK3 · BK4 가 서면 BK1 은 그 턴 빈 칸이 아니다 · BK3 · BK4 가 함께면 선택지가 적은 쪽(같으면 BK3 — B-8).
- 고르기(NQ2): ① 이번 턴에 새로 든 항목에 딸린 것 ② 이번 턴의 새 `guess`(BK5) ③ 앞 턴부터 남은 것 가운데 `focus` · 가장 최근
  대상의 것 ④ 그 밖. 같은 급이면 가장 최근 턴 · 같은 턴이면 BK 순서. 결정론(같은 장부 → 같은 `ask`).
- 묻지 않는 경우: NQ4 같은 빈 칸(slot + about)은 `CHAT_ASK_MAX` 번까지 · NQ5 앞 턴 `ask` 에 답하지 않았으면(새 항목 무관 —
  코디 W-2) · NQ6 안전 종류를 읽은 턴 · NQ7 세 종료 상태 · NQ9 선택지 0.
"""

from __future__ import annotations

from clinic_chat.consult import state as st

SAFETY_KINDS = ("adverse", "contraindication", "avoid")  # NQ6
BK_ORDER = ("BK2", "BK2s", "BK3", "BK4", "BK5", "BK1")


def _blank(bk: str, slot: str, about: str, options: list[dict], turn: int, asked_n: int) -> dict:
    return {"bk": bk, "slot": slot, "about": about, "options": options, "options_n": len(options), "asked_n": asked_n,
            "turn": turn}


def _asked_n(obj: dict, slot: str, about: str) -> int:
    return sum(1 for a in obj["asked"] if a["slot"] == slot and a["about"] == about)


def _born(it: dict) -> int:
    """그 항목이 축으로(또는 추정으로) 마지막에 든 턴."""
    return max([h["turn"] for h in it.get("history") or []] or [0])


def count(obj: dict, plan, list_shown: bool) -> list[dict]:
    """BK1 ~ BK5 + 부위만(검수 W-1 (가)) — `plan` = 이번 턴 객체 조회 결과(선택지 · 하위 고민 · 하위 부위)."""
    out: list[dict] = []
    f = st.focus_item(obj)
    subjects = st.axis_items(obj, "subjects")
    concerns = st.axis_items(obj, "concerns")
    if subjects and not concerns:  # BK2
        for s in subjects:
            opts = [_opt(o) for o in plan.options.get(st.node(s), [])]
            out.append(_blank("BK2", "concern", st.node(s), opts, _born(s), _asked_n(obj, "concern", st.node(s))))
    if f is None:  # 부위만 대상(검수 W-1 (가)) — focus 가 있으면 서지 않는다
        for s in st.axis_items(obj, "sites"):
            if st.node(s) in plan.options:
                opts = [_opt(o) for o in plan.options[st.node(s)]]
                out.append(_blank("BK2s", "concern", st.node(s), opts, _born(s), _asked_n(obj, "concern", st.node(s))))
    if f is not None:
        about = st.node(f)
        b3 = b4 = None
        if plan.sub_concerns:  # BK3
            b3 = _blank("BK3", "concern_narrow", about, [_opt(o) for o in plan.sub_concerns], _born(f),
                        _asked_n(obj, "concern_narrow", about))
        if len(plan.sub_sites) >= 2 and not st.axis_items(obj, "sites"):  # BK4(부위 칸이 없으면 서지 않는다)
            b4 = _blank("BK4", "site", about, [_opt(o) for o in plan.sub_sites], _born(f), _asked_n(obj, "site", about))
        if b3 and b4:  # 코디 W-4 — 선택지가 적은 쪽 · 같으면 BK3(B-8)
            out.append(b3 if b3["options_n"] <= b4["options_n"] else b4)
        elif b3 or b4:
            out.append(b3 or b4)
        elif not subjects and list_shown:  # BK1(W-4 — BK3 · BK4 가 서면 그 턴 빈 칸이 아니다)
            out.append(_blank("BK1", "interest", about, [], _born(f), _asked_n(obj, "interest", about)))
    focus_sites = {x["code"] for x in obj.get("concern_sites") or []
                   if obj.get("focus") and obj.get("focus") in (x.get("concern_code"), x.get("asked"))}
    for s in ("concerns", "sites", "subjects"):  # BK5
        for x in obj[s]:
            if s == "sites" and x.get("code") in focus_sites:  # 0.3 CS7 — focus 고민의 고민 부위면 빈 칸 아님
                continue
            if x.get("state") == "guess":
                out.append(_blank("BK5", "confirm", st.node(x), [_opt(x)], _born(x), _asked_n(obj, "confirm", st.node(x))))
    return out


def _opt(o: dict) -> dict:
    return {"table": o["table"], "code": o["code"], "name": o.get("name") or "", "layer": o.get("layer") or "",
            **({"holder": o["holder"]} if o.get("holder") else {})}


def _rank(b: dict, obj: dict, turn: int, new_nodes: set[str], new_guess: set[str]) -> tuple:
    if b["bk"] == "BK5":
        grade = 2 if b["about"] in new_guess else 4
    elif b["about"] in new_nodes:
        grade = 1
    else:
        f = obj.get("focus")
        subs = st.axis_items(obj, "subjects")
        latest = max(subs, key=_born) if subs else None
        grade = 3 if b["about"] == (f"concern:{f}" if f else None) or (latest and b["about"] == st.node(latest)) else 4
    return grade, -b["turn"], BK_ORDER.index(b["bk"])


def pick(blanks: list[dict], obj: dict, turn: int, *, new_nodes: set[str], new_guess: set[str], prev_ask: dict | None,
         end_state: str | None, kinds_read: list[str], ask_max: int) -> tuple[dict | None, str]:
    """NQ1 · NQ2 · NQ4 ~ NQ9 → (`ask` | None, 까닭). 까닭 = NQ2 의 급(`NQ2-1` …) 또는 None 의 규칙."""
    if end_state in st.TERMINAL:
        return None, "NQ7"
    if any(k in SAFETY_KINDS for k in kinds_read):
        return None, "NQ6"
    if prev_ask is not None and prev_ask.get("answered") is False:
        return None, "NQ5"
    live = [b for b in blanks if b["asked_n"] < ask_max]  # NQ4
    if not live and blanks:
        return None, "NQ4"
    live = [b for b in live if b["slot"] == "interest" or b["options"]]  # NQ9
    if not live:
        return None, "NQ9" if blanks else "none"
    best = min(live, key=lambda b: _rank(b, obj, turn, new_nodes, new_guess))
    grade = _rank(best, obj, turn, new_nodes, new_guess)[0]
    ask = {"slot": best["slot"], "about": best["about"], "options": best["options"], "why": f"NQ2-{grade}",
           "bk": best["bk"]}
    return ask, ask["why"]


def ledger_view(blanks: list[dict]) -> list[dict]:
    return [{"slot": b["slot"], "about": b["about"], "options_n": b["options_n"], "asked_n": b["asked_n"], "bk": b["bk"]}
            for b in blanks]


def record(obj: dict, ask: dict, turn: int) -> dict:
    """NQ3 — 준 `ask` 를 `asked[]` 에(그 `options[]` 가 다음 턴의 걸린 선택지)."""
    a = {"slot": ask["slot"], "about": ask["about"], "turn": turn,
         "options": [{k: o[k] for k in ("table", "code", "name")} for o in ask["options"]], "answered": None}
    obj["asked"].append(a)
    return a
