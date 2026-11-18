"""상담 객체 — 대화마다 하나 · 턴마다 갱신 · diff(SPEC-005 §2 · §3-2 UP1 ~ UP9 · §5 RV1 ~ RV9 · WORK-012 WP-1 · WP-2).

- 객체 = JSON 으로 그대로 적히는 dict(`empty()`). 항목 = `{table, code, name, state, source, history}` · 바람의 정보 종류만
  `{kind, state, source, history}`(N-6). `blanks[]` 는 저장하지 않고 턴마다 센다(`blanks.count`).
- 0.4(SPEC-008 §2-4 OU1 ~ OU12): 재료는 **검증 뒤 이해 출력 하나**(`verify.Accepted` — VF12) · 앱이 글에서 낱말을 따로 뽑지
  않는다. 급 = `sure` true → `said` · false → `guess`(OU2) · `how` = 앞 `ask` 선택지 → `option` · `follow` 턴 → `carried` ·
  그 밖 `text`(OU3) · `follow` 대상이 객체에 있으면 급 그대로(OU4 — `KEEP`).
- 같은 이름의 고민 · 부위(코디 W-2 · OU3): 이번 턴에 고민과 **이름이 같은** 부위 대상은 `sites[]` 에 넣되 `source.pair_concern` 에
  짝 고민 코드 — 부위 거르개(QS3)는 그 짝 고민이 `focus` 일 때만(`site_filter`).
- 조회 축(RV1)은 `axis_items()` 하나 — `said` · `confirmed` 만 낸다. `guess` 는 축 · `focus` · 거르개가 아니다.
- 거둠(OU8) = 검증 뒤 `withdraw[]` 집합(거둠용 `resolve` · Jev 요청 없음) · `focus` = 이번 턴 `said` 고민 가운데 `targets` 순서로
  마지막(OU9) · 객체 항목 `source` = `{turn, how, from_ask, sure}`(OU12 — `noul` · `decision` · `matched` · `covered_by` 없음).
"""

from __future__ import annotations

import copy

from clinic_chat.walk import end_state as es

SLOT_OF = {"concern": "concerns", "site": "sites", "modality": "subjects", "procedure": "subjects",
           "procedure_case": "subjects", "product": "subjects", "effect": "wishes", "method": "wishes",
           "material": "wishes"}  # UP2 — 그 밖의 표(adverse · condition · anatomy · offer · package)는 넣지 않는다
ITEM_SLOTS = ("concerns", "sites", "subjects", "wishes")
KEEP = "keep"  # 0.3 검수 W-3 ⓐ — 관찰 급 「그대로」(객체 칸의 state 를 안 바꾼다 · 객체에 적히지 않는 값)
WISH_KINDS = ("recovery", "outcome")  # §2-3 이 바람으로 둔 정보 종류(B-3)
KIND_CODES_14 = ("overview", "mechanism", "indication", "outcome", "lineup", "adverse", "recovery", "contraindication",
                 "avoid", "combo", "same_day", "order_interval", "package", "offer")  # SPEC-001 §5-8(N-6 닫힌 목록)
STATES = ("said", "guess", "confirmed", "withdrawn")
HOWS = ("text", "option", "derived", "carried", "view")  # 0.3 B-2 — 누른 하위 고민(손님이 말한 것이 아니다)
AXIS = ("said", "confirmed")
DIFF_OPS = ("add", "state", "withdraw", "focus", "shown", "asked")
TERMINAL = (es.OUT, es.NO_ENTRY, es.ABORTED)  # 세 종료 상태(코디 W-6)


def empty() -> dict:
    return {"concerns": [], "focus": None, "sites": [], "concern_sites": [], "subjects": [], "procedures": [],
            "wishes": [], "shown": {"lists": [], "info": [], "synergy": []}, "asked": []}


def node(it: dict) -> str:
    return f"{it['table']}:{it['code']}"


def find(obj: dict, table: str, code: str) -> dict | None:
    slot = SLOT_OF.get(table)
    if not slot:
        return None
    return next((x for x in obj[slot] if x.get("table") == table and x.get("code") == code), None)


def axis_items(obj: dict, slot: str) -> list[dict]:
    """RV1 — 조회 축 = `said` · `confirmed` 만(다른 모듈은 이것만 쓴다)."""
    return [x for x in obj.get(slot) or [] if x.get("table") and x.get("state") in AXIS]


def focus_item(obj: dict) -> dict | None:
    f = obj.get("focus")
    return next((x for x in obj["concerns"] if x["code"] == f), None) if f else None


def observe_accepted(acc, prev_ask: dict | None, obj: dict | None = None) -> list[dict]:
    """OU1 ~ OU4 — 검증 뒤 대상 하나 = 관찰 하나(`order` = `targets` 안 자리 — OU9 재료)."""
    opts = {(o.get("table"), o.get("code")) for o in (prev_ask or {}).get("options") or []}
    out: list[dict] = []
    for i, t in enumerate(acc.targets):
        table, code = t["table"], t["code"]
        if table not in SLOT_OF:
            continue
        st = "said" if t.get("sure") else "guess"
        from_ask = None
        if (table, code) in opts:
            how, from_ask = "option", (prev_ask or {}).get("turn")
        elif acc.kind == "follow":
            how = "carried"
            if obj is not None and find(obj, table, code) is not None:  # OU4 — 객체에 있으면 급 그대로
                st = KEEP
        else:
            how = "text"
        out.append({"table": table, "code": code, "name": t.get("name") or "", "state": st, "how": how,
                    "from_ask": from_ask, "sure": bool(t.get("sure")), "order": i})
    return out


def _new_item(o: dict, turn: int) -> dict:
    return {"table": o["table"], "code": o["code"], "name": o["name"], "state": o["state"],
            "source": {"turn": turn, "how": o["how"], "from_ask": o["from_ask"], "sure": o.get("sure")},
            "history": [{"turn": turn, "state": o["state"]}]}


def _again(old: str, seen: str) -> str:
    """UP6 — 다시 나옴: said → confirmed · withdrawn → 다시 말함 = said(추정이면 guess).
    0.3 FX1(SPEC-006 · 0.2 검수 W-1): guess → confirmed 는 다시 나온 관찰이 `said` 급(`target` · 걸린 선택지 yes · BK5 답 yes)
    일 때만 — `guess` 급으로 다시 나오면 `state` 그대로(`history` 에 턴만 더한다)."""
    if seen == KEEP:  # 0.3 검수 W-3 ⓐ — 줄임으로 다시 부른 앞 대상은 급을 바꾸지 않는다
        return old
    if old == "withdrawn":
        return seen
    if old == "guess" and seen != "said":
        return "guess"
    return "confirmed"


def update(obj: dict, obs: list[dict], wish_kinds: list[str], turn: int, withdraw: set[str]) -> list[dict]:
    """L4 — 관찰 · 거둠(검증 뒤 `withdraw` 집합 — OU8) → 객체(제자리 고침) · diff(UP9). 세 종료 상태 턴에도 돈다(코디 W-6)."""
    diff: list[dict] = []
    touched: list[tuple[dict, dict]] = []
    for o in obs:
        n = f"{o['table']}:{o['code']}"
        if n in withdraw:  # RV3 — 이번 글의 이름이라도 거둠 yes 면 안 넣는다(있으면 아래에서 withdrawn)
            continue
        it = find(obj, o["table"], o["code"])
        slot = SLOT_OF[o["table"]]
        if it is None:
            if o["state"] == KEEP:  # 객체에 없는 앞 대상(생기지 않는 꼴) — 추정으로 둔다
                o = {**o, "state": "guess"}
            it = _new_item(o, turn)
            obj[slot].append(it)
            diff.append({"op": "add", "slot": slot, "table": o["table"], "code": o["code"], "from": None,
                         "to": it["state"], "why": o["how"]})
        else:
            new = _again(it["state"], o["state"])
            it["history"].append({"turn": turn, "state": new})
            if o["from_ask"] is not None and it["source"].get("from_ask") is None:
                it["source"]["from_ask"] = o["from_ask"]
            if new != it["state"]:
                diff.append({"op": "state", "slot": slot, "table": o["table"], "code": o["code"], "from": it["state"],
                             "to": new, "why": o["how"]})
                it["state"] = new
        touched.append((it, o))
    for n in sorted(withdraw):
        table, _, code = n.partition(":")
        it = find(obj, table, code)
        if it is not None and it["state"] != "withdrawn":
            diff.append({"op": "withdraw", "slot": SLOT_OF[table], "table": table, "code": code, "from": it["state"],
                         "to": "withdrawn", "why": "retract"})
            it["state"] = "withdrawn"
            it["history"].append({"turn": turn, "state": "withdrawn"})
    _pair_sites(touched)
    for k in wish_kinds:
        it = next((x for x in obj["wishes"] if x.get("kind") == k), None)
        if it is None:
            obj["wishes"].append({"kind": k, "state": "said", "source": {"turn": turn, "how": "text"},
                                  "history": [{"turn": turn, "state": "said"}]})
            diff.append({"op": "add", "slot": "wishes", "table": None, "code": k, "from": None, "to": "said",
                         "why": "kind"})
        else:
            it["history"].append({"turn": turn, "state": "confirmed"})
            if it["state"] != "confirmed":
                diff.append({"op": "state", "slot": "wishes", "table": None, "code": k, "from": it["state"],
                             "to": "confirmed", "why": "kind"})
                it["state"] = "confirmed"
    old = obj.get("focus")
    obj["focus"] = _focus(obj, touched, turn)
    if obj["focus"] != old:
        diff.append({"op": "focus", "slot": "focus", "table": "concern", "code": obj["focus"], "from": old,
                     "to": obj["focus"], "why": None})
    return diff


def _pair_sites(touched: list[tuple[dict, dict]]) -> None:
    """코디 W-2 · OU3 — 이번 턴에 고민과 이름이 같은 부위 대상에 짝 고민 코드(0.4 — 글자 자리 재료가 없어 이름으로)."""
    cons = {(o["name"] or "").strip(): it["code"] for it, o in touched
            if o["table"] == "concern" and it["state"] in AXIS and (o["name"] or "").strip()}
    for it, o in touched:
        if o["table"] == "site" and (o["name"] or "").strip() in cons:
            it["source"]["pair_concern"] = cons[(o["name"] or "").strip()]


def _focus(obj: dict, touched: list[tuple[dict, dict]], turn: int) -> str | None:
    """OU9 · RV6 · RV7 — 이번 턴에 `said` 급으로 든(다시 든) 축 고민 가운데 `targets` 순서로 마지막 것(앱이 다시 정렬하지
    않는다) · 이번 턴 고민이 없으면 그대로 · 거두면 남은 가장 최근."""
    now = [(it, o) for it, o in touched if o["table"] == "concern" and it["state"] in AXIS and o["state"] != "guess"]
    if now:
        return max(now, key=lambda x: x[1]["order"])[0]["code"]
    cur = focus_item(obj)
    if cur is not None and cur["state"] in AXIS:
        return cur["code"]
    left = axis_items(obj, "concerns")
    if not left:
        return None

    def last_said(x: dict) -> int:
        return max([h["turn"] for h in x["history"] if h["state"] in AXIS] or [0])
    return max(enumerate(left), key=lambda e: (last_said(e[1]), e[0]))[1]["code"]


def mark_answered(obj: dict, turn: int, diff: list[dict], subjects_before: set[str]) -> dict | None:
    """NQ3 · NQ5(코디 W-2 · 검수 W-4) — 바로 앞 턴의 `ask` 에 답했나: 선택지가 이번 턴 대상으로 확정됨(걸린 선택지 yes 또는
    이번 글의 `target`) · BK5 는 그 추정이 확인되거나 거둬짐 · BK1(선택지 없음)은 `subjects[]` 에 새 항목(B-9)."""
    last = obj["asked"][-1] if obj["asked"] else None
    if not last or last.get("turn") != turn - 1 or last.get("answered") is not None:
        return None
    hit = {(d["table"], d["code"]) for d in diff if d["op"] in ("add", "state", "withdraw")
           and (d["op"] == "withdraw" or d["to"] in AXIS)}
    hit |= {(x["table"], x["code"]) for s in ("concerns", "sites", "subjects") for x in obj[s]
            if x.get("source", {}).get("from_ask") == last["turn"] and x["state"] in AXIS}
    if last["slot"] == "interest":
        ok = bool({node(x) for x in obj["subjects"]} - subjects_before)
    else:
        ok = any((o.get("table"), o.get("code")) in hit for o in last.get("options") or [])
    last["answered"] = ok
    diff.append({"op": "asked", "slot": "asked", "table": None, "code": last["slot"], "from": None, "to": ok,
                 "why": "answered" if ok else "not_answered"})
    return last


def snapshot(obj: dict) -> dict:
    return copy.deepcopy(obj)
