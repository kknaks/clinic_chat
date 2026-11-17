"""상담 객체 — 대화마다 하나 · 턴마다 갱신 · diff(SPEC-005 §2 · §3-2 UP1 ~ UP9 · §5 RV1 ~ RV9 · WORK-012 WP-1 · WP-2).

- 객체 = JSON 으로 그대로 적히는 dict(`empty()`). 항목 = `{table, code, name, state, source, history}` · 바람의 정보 종류만
  `{kind, state, source, history}`(N-6). `blanks[]` 는 저장하지 않고 턴마다 센다(`blanks.count`).
- 재료는 **걷기 trace 뿐**(UP1): `front.trace_front["targets"]`(후보마다 decision · noul) · `trace_entry["resolve_candidates"]`
  (맞은 조각 · 시작 자리) · `trace_front["kinds"]`. 앱이 글에서 낱말을 따로 뽑지 않는다.
- 확실함(UP3 · 코디 W-1): `target` → `said` · `uncertain_kept` → `guess` · `named` → **넣지 않는다**(trace 에만) · `dropped` ·
  `overlap` → 넣지 않는다 · `carried`(든 후보 · 걸린 선택지의 yes) → `said`(`how` = `carried` · `option` — B-4).
- 같은 이름의 고민 · 부위(코디 W-2): 같은 조각(`span` · `start`)에서 고민과 함께 대상이 된 부위는 `sites[]` 에 넣되
  `source.pair_concern` 에 짝 고민 코드 — 부위 거르개(QY1 · QY5)는 그 짝 고민이 `focus` 일 때만(`filter_sites`).
- 조회 축(RV1)은 `axis_items()` 하나 — `said` · `confirmed` 만 낸다. `guess` 는 축 · `focus` · 거르개가 아니다.
- 거둠(RV2 · RV3 · N-5): 상담 쪽이 `resolve` 를 한 번 더 부르고 Jev 요청 하나(`retract`) — 걷기와 함께 띄운다.
"""

from __future__ import annotations

import asyncio
import copy
import time

from clinic_chat.walk import end_state as es
from clinic_chat.walk.jev_requests import ask_nouls, base_state

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


def filter_sites(obj: dict) -> list[dict]:
    """QY1 · QY5 의 부위 거르개 — 축 부위 가운데 짝 고민이 없거나 짝 고민이 지금 `focus` 인 것(코디 W-2)."""
    f = obj.get("focus")
    return [x for x in axis_items(obj, "sites") if (x.get("source") or {}).get("pair_concern") in (None, f)]


def _decision_state(t: dict) -> tuple[str, str] | None:
    dec = t.get("decision")
    if dec == "target" and t.get("by") == "abbr+carry":
        return KEEP, "abbr_carry"  # 0.3 검수 W-3 ⓐ — 앞 대상의 줄임: 객체 칸의 원래 급 그대로
    if dec == "target":
        return "said", "text"
    if dec == "uncertain_kept":
        return "guess", "text"
    if dec == "carried":
        return "said", ("option" if t.get("option_from") is not None else "carried")
    return None  # named(W-1) · dropped · overlap — 객체에 안 넣는다


def observe(w, prev_ask: dict | None) -> tuple[list[dict], list[str]]:
    """걷기 trace → 이번 턴의 관찰(UP1 · UP2 · UP3) · 바람 정보 종류(B-3)."""
    f = w.front
    if f is None:
        return [], []
    tf = f.trace_front or {}
    rc = {x.get("id"): x for x in (f.trace_entry or {}).get("resolve_candidates") or []}
    opts = {(o.get("table"), o.get("code")) for o in (prev_ask or {}).get("options") or []}
    out: list[dict] = []
    for i, t in enumerate(tf.get("targets") or []):
        tid = str(t.get("id") or "")
        table, _, code = tid.partition(":")
        if table not in SLOT_OF or not code:
            continue
        ds = _decision_state(t)
        if ds is None:
            continue
        st, how = ds
        r = rc.get(tid) or {}
        from_ask = t.get("option_from")
        if how == "text" and (table, code) in opts:  # 검수 W-4 — 선택지를 이름 그대로 답함
            from_ask = (prev_ask or {}).get("turn")
        out.append({"table": table, "code": code, "name": t.get("name") or "", "state": st, "how": how,
                    "matched": r.get("span"), "covered_by": t.get("covered_by"), "noul": t.get("noul"),
                    "decision": t.get("decision"), "from_ask": from_ask, "start": r.get("start"), "order": i})
    kinds = [k["code"] for k in tf.get("kinds") or [] if k.get("decision") == "read" and k.get("by") == "yes"
             and k.get("code") in WISH_KINDS]
    return out, kinds


def _new_item(o: dict, turn: int) -> dict:
    return {"table": o["table"], "code": o["code"], "name": o["name"], "state": o["state"],
            "source": {"turn": turn, "how": o["how"], "matched": o["matched"], "covered_by": o["covered_by"],
                       "noul": o["noul"], "decision": o["decision"], "from_ask": o["from_ask"]},
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


def update(obj: dict, obs: list[dict], wish_kinds: list[str], turn: int, retract_yes: set[str]) -> list[dict]:
    """L4 — 관찰 · 거둠 → 객체(제자리 고침) · diff(UP9). 세 종료 상태 턴에도 돈다(코디 W-6)."""
    diff: list[dict] = []
    touched: list[tuple[dict, dict]] = []
    for o in obs:
        n = f"{o['table']}:{o['code']}"
        if n in retract_yes:  # RV3 — 이번 글의 이름이라도 거둠 yes 면 안 넣는다(있으면 아래에서 withdrawn)
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
    for n in sorted(retract_yes):
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
    """코디 W-2 — 같은 조각(`matched` · `start`)에서 고민과 함께 대상이 된 부위에 짝 고민 코드."""
    cons = {(o["matched"], o["start"]): it["code"] for it, o in touched
            if o["table"] == "concern" and it["state"] in AXIS and o["matched"] and o["start"] is not None}
    for it, o in touched:
        if o["table"] == "site" and (o["matched"], o["start"]) in cons:
            it["source"]["pair_concern"] = cons[(o["matched"], o["start"])]


def _focus(obj: dict, touched: list[tuple[dict, dict]], turn: int) -> str | None:
    """RV5 ~ RV7 — 이번 턴에 말한(다시 말한) 축 고민 가운데 글에서 뒤에 나온 것(같으면 허브 순서) · 거두면 남은 가장 최근."""
    now = [(it, o) for it, o in touched if o["table"] == "concern" and it["state"] in AXIS]
    if now:
        best = max(now, key=lambda x: (x[1]["start"] if x[1]["start"] is not None else -1, -x[1]["order"]))
        return best[0]["code"]
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


def retract_items(obj: dict, candidates: list[dict]) -> list[dict]:
    """RV2 · RV3 — 거둠을 물을 항목 = 객체의 said · confirmed · guess 항목 + 이번 글의 후보 가운데 객체 칸에 들 표의 것."""
    out: list[dict] = []
    seen: set[str] = set()
    for s in ("concerns", "sites", "subjects", "wishes"):
        for x in obj[s]:
            if x.get("table") and x["state"] in ("said", "confirmed", "guess") and node(x) not in seen:
                seen.add(node(x))
                out.append({"id": node(x), "name": x.get("name") or "", "table": x["table"]})
    for c in candidates:
        t, code = c.get("entity_table"), c.get("entity_code")
        n = f"{t}:{code}"
        if t in SLOT_OF and code and n not in seen:
            seen.add(n)
            out.append({"id": n, "name": c.get("entity_name") or c.get("text") or "", "table": t})
    return out


def has_items(obj: dict) -> bool:
    return any(x.get("table") and x["state"] in ("said", "confirmed", "guess")
               for s in ("concerns", "sites", "subjects", "wishes") for x in obj[s])


async def retract(question: str, obj: dict, d, instr: str, *, yes_at: float, extra: dict[str, str] | None = None,
                  state_extra: dict | None = None, items_on: bool = True) -> dict:
    """RV2 — 거둠용 `resolve` 한 번(N-5 · 객체 조회 셈 밖) + Jev 요청 하나(32 를 넘으면 묶음 수만큼). 실패하면 거둠 0(B-5).

    0.3(SPEC-006 TK3 · 검수 N-3): `extra`(턴 종류 Noul 셋)는 **첫 묶음에만** 싣는다 — 거둠 항목이 0 이어도 묻는다(요청 하나).
    `state_extra` = Jev state 에 더할 칸(`prev` · `consult`) · `items_on=False` = 거둠은 묻지 않고 `extra` 만.
    반환 `extra` = 그 값(오류면 None)."""
    t0 = time.perf_counter()
    out: dict = {"items": [], "requests": 0, "resolve_ms": None, "ms": None, "error": None, "extra": None}
    try:
        t1 = time.perf_counter()
        res = await d.hub.resolve(question)
        out["resolve_ms"] = round((time.perf_counter() - t1) * 1000, 1)
        items = retract_items(obj, res.get("candidates") or []) if items_on else []
        if items or extra:
            b = d.settings.jev_batch_size
            state0 = base_state(question, res.get("build"), **(state_extra or {}))
            vals, ev, n_req = await ask_nouls(d.jev, state0, items[:b], "retract", instr, b, extra or None)
            if len(items) > b:
                v2, _, n2 = await ask_nouls(d.jev, state0, items[b:], "retract", instr, b)
                vals, n_req = vals + v2, n_req + n2
            out["requests"] = n_req
            out["extra"] = ev if extra else None
            for it, v in zip(items, vals, strict=True):
                table, _, code = it["id"].partition(":")
                out["items"].append({"table": table, "code": code, "noul": v,
                                     "decision": "withdraw" if v >= yes_at else "keep"})
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — B-5: 거둠은 보조 판단 · 턴을 죽이지 않는다
        out["items"] = []
        out["error"] = {"kind": type(e).__name__}
    out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def retract_yes(r: dict | None) -> set[str]:
    return {f"{x['table']}:{x['code']}" for x in (r or {}).get("items") or [] if x["decision"] == "withdraw"}


def snapshot(obj: dict) -> dict:
    return copy.deepcopy(obj)
