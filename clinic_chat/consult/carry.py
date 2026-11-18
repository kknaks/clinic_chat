"""이어 묻기 — 앞 턴 대상 → 맨 앞 판단의 든 후보(SPEC-004 §5-2 CR1 · U-12 (a) · 검수 W-3 ③).

든 후보 = 장부에서 「대상이 있는」 가장 가까운 앞 턴 `CHAT_CARRY_TURNS` 개의 `targets[]`. 종료 상태가 범위 밖 · 입구 없음 ·
실행 중단인 턴과 `error` 가 있는 턴은 건너뛴다. 앞 턴 **리스트의 시술**은 넣지 않는다(U-12 — 0.2).
yes 일 때만 대상이 되는 규칙 · 지시 절은 `walk.front(carry=…)` 가 맡는다(CR2 ~ CR6).
"""

from __future__ import annotations

from clinic_chat.walk import end_state as es

SKIP_END = (es.OUT, es.NO_ENTRY, es.ABORTED)


def candidates(turns: list[dict], n_turns: int) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    used = 0
    for t in reversed(turns):
        if used >= n_turns:
            break
        if t.get("error") is not None or t.get("end_state") in SKIP_END or not t.get("targets"):
            continue
        used += 1
        for x in t["targets"]:
            key = (x.get("table"), x.get("code"))
            if not x.get("code") or key in seen:
                continue
            seen.add(key)
            out.append({"table": x["table"], "code": x["code"], "name": x.get("name") or "", "from_turn": t["turn"]})
    return out


def with_options(carried: list[dict], asked: list[dict], turn: int) -> list[dict]:
    """0.2 UP4 — 든 후보(앞 턴 대상 · 장부 `targets[]` 그대로) **뒤에** 걸린 선택지 = 바로 앞 턴에 준 `ask` 의 `options[]`.

    항목 꼴 = 든 후보와 같고 `origin: "option"` 이 붙는다(SPEC-005 §9 ST3 — `walk()` 인자를 늘리지 않는다). 같은 (표, 코드)가
    든 후보에 이미 있으면 다시 넣지 않고 그 항목에 `origin` 을 단다(B-4 · N-3 — 장부 `carried[]` 에는 그대로 남는다).
    걸린 선택지는 한 턴만 산다(UP5) — `asked[]` 의 마지막 것이 바로 앞 턴의 것일 때만."""
    out = [dict(x) for x in carried]
    last = asked[-1] if asked else None
    if not last or last.get("turn") != turn - 1:
        return out
    idx = {(x["table"], x["code"]): x for x in out}
    for o in last.get("options") or []:
        key = (o.get("table"), o.get("code"))
        if not o.get("code"):
            continue
        if key in idx:
            idx[key]["origin"] = "option"
            idx[key]["also_carried"] = True
            continue
        x = {"table": o["table"], "code": o["code"], "name": o.get("name") or "", "from_turn": last["turn"],
             "origin": "option"}
        idx[key] = x
        out.append(x)
    return out


def alive_in_object(carried: list[dict], obj: dict, slot_of: dict[str, str]) -> list[dict]:
    """0.3 TW7 · 코디 W-3 — 켠 상태의 든 후보: 객체 칸에 드는 표의 것만 객체의 생사로 거른다(객체에 없거나 `withdrawn` 이면
    뺀다) · 그 밖의 표(package · adverse · condition · anatomy · offer)는 0.1 CR1 그대로 싣는다."""
    out = []
    for x in carried:
        slot = slot_of.get(x.get("table") or "")
        if slot is None:
            out.append(x)
            continue
        it = next((y for y in obj.get(slot) or [] if y.get("table") == x["table"] and y.get("code") == x["code"]), None)
        if it is not None and it.get("state") != "withdrawn":
            out.append(x)
    return out
