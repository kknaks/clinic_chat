"""시너지 먼저 · 같은 날 제약 표지(SPEC-005 §7 SY1 ~ SY10 · WORK-012 WP-5 · 사용자 U5-2 (a) · U5-3 (a) · U5-4 (a1) · U5-5 (a)).

- SY1: 축 대상(`said` · `confirmed`)과 `focus` 고민이 **둘 다** 있을 때, 그 대상의 시너지를 아직 안 읽었으면(`shown.synergy[]`)
  그 턴에 읽는다. `CHAT_SYNERGY=off` 면 통째로 안 한다.
- QY4 = `pair(a = 대상, classes = [보완])` — 재료로 싣는 조회는 이것뿐(SY6). **QY4b**(U5-4 (a1)) = 같은 대상의
  `pair(classes = [회피, 간격, 순서])` — 「회피」 갈래 또는 `same_day` = 「불가」 행이 있는 상대에 표지 `same_day_caution` 만
  세운다. QY4b 의 행은 LLM 재료 · 「실은 gid」 · `shown.info[]` · 화면 값 어디에도 가지 않는다(장부에는 조회 기록 ·
  표지 근거 gid 만).
- SY4: 상대마다 요약 줄 하나 · SY5: 리스트 **안** 상대만 LLM 줄 · 리스트 밖 상대는 화면 묶음 ③ 아래 수와 이름만. 「안」 은 코드로
  견준다 — 계열 = 리스트의 계열 묶음 코드 · 시술 = 리스트의 시술 행 코드 · 케이스 · 제품 = 그것이 닿는 시술 · 계열(B-6 ② —
  `pair` 행에 상대 쪽 층 칸이 없어 상대의 `scope` 를 읽는다 · `rule: "SY5"`).
- SY7: 상대 순서 = `pair` 행 순서에서 처음 나온 순서 · SY9: 한 대상의 조회 · LLM 줄은 한 번 — `focus` 가 바뀌면 화면 묶음만
  다시 짓는다(허브 재조회 0 — `shown.synergy[].others` 에 든 상대 목록으로) · SY10: 대상마다 따로.
"""

from __future__ import annotations

import hashlib

from clinic_chat.consult import state as st
from clinic_chat.consult.plan import Reader
from clinic_chat.walk.calls import Call
from clinic_chat.walk.ranges import PAIR_CLASSES

COMPLEMENT = PAIR_CLASSES["combo"]  # 「보완」(걷기의 갈래 표 그대로)
CAUTION_CLASS = PAIR_CLASSES["avoid"][0]  # 「회피」
# U5-4 (a1) — 회피 · 불가 행이 놓인 갈래 셋: 회피 · 간격 · 순서(WORK-012 §3-1 재셈)
CAUTION_CLASSES = (CAUTION_CLASS, PAIR_CLASSES["order_interval"][1], PAIR_CLASSES["order_interval"][0])
CAUTION_SAME_DAY = "\ubd88\uac00"  # 허브 `g_pair.same_day` 값 「불가」(앱 문장 아님)
REACH_TABLES = ("procedure_case", "product")  # B-6 ② — 닿는 시술 · 계열을 `scope` 로 읽는 상대


def pair_call(a: str, classes: tuple[str, ...]) -> Call:
    return Call("pair", {"a": a, "classes": list(classes)}, ("combo",), ("combo",))


def scope_call(n: str) -> Call:
    return Call("scope", {"node": n}, ("lineup",), ("procedure",))


def partner(row: dict, a: str) -> tuple[str, str, str]:
    side = row.get("a_side")
    if side not in ("from", "to"):
        side = "from" if f"{row.get('from_table')}:{row.get('from_code')}" == a else "to"
    o = "to" if side == "from" else "from"
    return str(row.get(f"{o}_table") or ""), str(row.get(f"{o}_code") or ""), str(row.get(f"{o}_name") or "")


def _uniq(xs) -> list:
    return list(dict.fromkeys(x for x in xs if x not in (None, "")))


def partners(rows: list[dict], a: str) -> list[dict]:
    """SY4 · SY7 — 보완 행 → 상대마다 하나(허브 행 순서에서 처음 나온 순서)."""
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        t, c, name = partner(r, a)
        if not c:
            continue
        p = out.setdefault((t, c), {"table": t, "code": c, "name": name, "rows": 0, "a_layer": [], "same_day": [],
                                    "goal_effects": [], "gids": [], "caution": False, "caution_gids": [],
                                    "reach": None})
        p["rows"] += 1
        p["gids"].append(str(r.get("gid")))
        p["a_layer"] = _uniq([*p["a_layer"], (r.get("a_layer") or {}).get("role")])
        p["same_day"] = _uniq([*p["same_day"], r.get("same_day")])  # SY3 — 빈칸은 싣지 않는다
        p["goal_effects"] = _uniq([*p["goal_effects"], r.get("goal_effects")])
    return list(out.values())


def caution(others: list[dict], rows_b: list[dict], a: str) -> None:
    """U5-4 (a1) — QY4b 행 가운데 「회피」 갈래 또는 `same_day` = 「불가」 인 행이 있는 상대 → 표지(근거 gid 만 남김)."""
    idx = {(p["table"], p["code"]): p for p in others}
    for r in rows_b:
        if r.get("question_class") != CAUTION_CLASS and r.get("same_day") != CAUTION_SAME_DAY:
            continue
        t, c, _ = partner(r, a)
        p = idx.get((t, c))
        if p is not None:
            p["caution"] = True
            p["caution_gids"].append(str(r.get("gid")))


def reach_of(holders: list[dict]) -> dict:
    procs = _uniq(h.get("holder_code") for h in holders if h.get("holder_table") == "procedure")
    mods = _uniq(h.get("holder_code") for h in holders if h.get("holder_table") == "modality")
    return {"procedures": procs, "modalities": mods}


def inside(p: dict, list_procs: set[str], list_mods: set[str]) -> bool:
    """SY5 — 리스트 안인가(코드로 · B-6)."""
    if p["table"] == "modality":
        return p["code"] in list_mods
    if p["table"] == "procedure":
        return p["code"] in list_procs
    r = p.get("reach") or {}
    return bool(set(r.get("procedures") or []) & list_procs or set(r.get("modalities") or []) & list_mods)


def line_key(a: str, p: dict) -> str:
    h = hashlib.sha256("\n".join(sorted(p["gids"])).encode("utf-8")).hexdigest()[:16]
    return f"QY4|{a}|{p['table']}:{p['code']}|{h}"


async def run(obj: dict, reader: Reader, turn: int, list_procs: set[str], list_mods: set[str], *,
              on: bool = True) -> dict:
    """L5 의 시너지 몫 → {lines(이번 턴 새 LLM 줄 재료), bundles(화면 묶음 ③), diff}."""
    out: dict = {"lines": [], "bundles": [], "diff": []}
    if not on:
        return out
    subjects = st.axis_items(obj, "subjects")
    f = obj.get("focus")
    if not f:
        return out
    shown = obj["shown"]["synergy"]
    for s in subjects:
        a = st.node(s)
        rec = next((x for x in shown if x["a"] == a), None)
        new = rec is None
        if new:
            ra_reads = await reader.get(pair_call(a, COMPLEMENT), "QY4")
            others = partners(list(ra_reads[0].ra.items) if ra_reads else [], a)
            rb_reads = await reader.get(pair_call(a, CAUTION_CLASSES), "QY4b")
            caution(others, list(rb_reads[0].ra.items) if rb_reads else [], a)
            for p in others:
                if p["table"] in REACH_TABLES:
                    sr = await reader.get(scope_call(f"{p['table']}:{p['code']}"), "SY5")
                    p["reach"] = reach_of(list(sr[0].ra.items) if sr else [])
            rec = {"a": a, "turn": turn, "others": others, "gids": [g for p in others for g in p["gids"]],
                   "caution_gids": [g for p in others for g in p["caution_gids"]], "by_focus": []}
            shown.append(rec)
            out["diff"].append({"op": "shown", "slot": "shown.synergy", "table": s["table"], "code": s["code"],
                                "from": None, "to": len(others), "why": "QY4"})
        ins = [p for p in rec["others"] if inside(p, list_procs, list_mods)]
        outs = [p for p in rec["others"] if not inside(p, list_procs, list_mods)]
        if not rec["by_focus"] or rec["by_focus"][-1]["concern_code"] != f:
            rec["by_focus"].append({"concern_code": f, "turn": turn, "in_list_n": len(ins), "outside_n": len(outs)})
        if new:
            for p in ins:
                out["lines"].append({"a": a, "a_name": s.get("name") or "", "partner": p})
        out["bundles"].append({"a_table": s["table"], "a_code": s["code"], "a_name": s.get("name") or "",
                               "inside": [{"table": p["table"], "code": p["code"], "name": p["name"],
                                           "caution": p["caution"]} for p in ins],
                               "outside": [{"table": p["table"], "code": p["code"], "name": p["name"],
                                            "caution": p["caution"]} for p in outs],
                               "outside_n": len(outs)})
    return out


def marks(bundles: list[dict], obj: dict) -> tuple[dict[str, bool], dict[str, bool]]:
    """리스트 안 상대 → 표지 자리: 시술 코드 → caution · 계열 코드 → caution(케이스 · 제품은 닿는 시술 · 계열에)."""
    reach = {}
    for rec in obj["shown"]["synergy"]:
        for p in rec["others"]:
            reach[(p["table"], p["code"])] = p.get("reach") or {}
    procs: dict[str, bool] = {}
    mods: dict[str, bool] = {}
    for b in bundles:
        for p in b["inside"]:
            if p["table"] == "procedure":
                procs[p["code"]] = procs.get(p["code"], False) or p["caution"]
            elif p["table"] == "modality":
                mods[p["code"]] = mods.get(p["code"], False) or p["caution"]
            else:
                r = reach.get((p["table"], p["code"])) or {}
                for c in r.get("procedures") or []:
                    procs[c] = procs.get(c, False) or p["caution"]
                for c in r.get("modalities") or []:
                    mods[c] = mods.get(c, False) or p["caution"]
    return procs, mods
