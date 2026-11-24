"""턴 메시지 블록「조건 · 명동점 진료항목 · 장바구니」(SPEC-010 §7-1 LC1 ~ LC9 · TP7 · N3 · N4 · CH3).

블록 = 머리 한 줄 + 줄마다 「머리 + JSON 하나」(값은 `$J` · 허브 · §5 · §6 결과 그대로 — 앱이 짓는 글 0). 머리 글은 프롬프트 `kos.labels`.
- 조건 줄(LC7): 지금 주제 · 주제 바뀜 · 앞 주제 요약 · 조건 칸 · 이번 턴 바뀐 칸 · 막은 조건 · 국가로 뺀 수 · 다음 질문 축.
- 진료항목 줄(LC1 ~ LC3): 목록 앞 `CHAT_KOS_LLM_ITEMS_MAX` + 장바구니 + 모달로 본 것 · 같은 코드 한 번 · 옵션 앞 5 · 넘친 수 한 줄.
- 장바구니 · 최저가 줄(LC8) · 같이 받기 줄(LC9 — 허브 `pair` 원본 행) · 경과 · 그 밖 부작용(본 진료항목 — N3 · N4) · 원리 그림 코드(CH3).
- 덧붙이기(LC5): 앞 턴 블록에 보낸 진료항목 · 장바구니(같은 꼴) · 쌍은 다시 싣지 않는다(장부 `llm.kos_block` 으로 셈).
"""

from __future__ import annotations

import hashlib
import json

from clinic_chat.consult.items_view import cond_names, cond_tuple
from clinic_chat.consult.kos_map import KosMap

OPTIONS_MAX = 5  # LC3
PAIRS_MAX = 10  # LC9
MAPPED = "\ub9e4\ud551\ub428"  # status 원문 「매핑됨」
TOGETHER = (
    "\uac19\uc774",
    "\ud568\uaed8",
    "\ub3d9\uc2dc\uc5d0",
)  # 같이 받기 표지 「같이」 · 「함께」 · 「동시에」(WP-6 검수 S-2)  — SPEC-012 부터 판정에 안 씀(옛 시험 · 기록용)
TOGETHER_KINDS = frozenset({"combo", "same_day"})  # SPEC-012 HD1 — 같이 받기 · 같은 날 물음 = 1차 이해 kinds(소넷 판단)
PAIR_KEEP = (
    "from_name",
    "to_name",
    "kind",
    "question_class",
    "level_text",
    "same_day",
    "same_day_text",
    "interval_text",
    "order",
    "text",
    "conflict_id",
    "conflict_side",
    "goal_effects",
)


def _j(x) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def sent_before(turns: list[dict]) -> dict:
    """LC5 — 앞 턴 블록이 실은 것(장부 `llm.kos_block`)."""
    codes: set[str] = set()
    pairs: set[str] = set()
    cart_sig = None
    for t in turns:
        if not (t.get("llm") or {}).get("answered"):
            continue  # WP-6 검수 P2-1 — 답이 실패한 턴의 블록은 모델이 못 봤다
        kb = (t.get("llm") or {}).get("kos_block") or {}
        codes |= set(kb.get("codes") or [])
        pairs |= set(kb.get("pairs") or [])
        if kb.get("cart_sig"):
            cart_sig = kb["cart_sig"]
    return {"codes": codes, "pairs": pairs, "cart_sig": cart_sig}


def item_line(kos: KosMap, code: str) -> dict:
    """LC2 · LC3 — 진료항목 줄(원문 칸 · 옵션 앞 5)."""
    it = kos.item[code]
    h = it["hub"]
    opts = []
    keys = list(kos.covers_of.get(code, ()))
    for k in keys[:OPTIONS_MAX]:
        p, o = kos.option_product[k], kos.option[k]
        row = {
            "product": p.get("title"),
            "title": o.get("title"),
            "type": o.get("type"),
            "count": o.get("count"),
            "price_amount": o.get("price_amount"),
            "event_price": o.get("event_price"),
            "is_event": p.get("is_event"),
            "closed": bool(p.get("closed") or o.get("closed")),
        }
        if o.get("type") == "group":
            row["covers"] = [kos.item[c].get("kos_item_name") for c in o.get("covers") or [] if c in kos.item]
        opts.append(row)
    out = {
        "code": code,
        "name": it.get("kos_item_name"),
        "kos_procedure": it.get("kos_procedure"),
        "medicine": it.get("kos_medicine"),
        "machine": it.get("kos_machine"),
        "unit": it.get("kos_unit"),
        "status": it.get("status"),
        "case_codes": h.get("case_codes"),
        "procedure_codes": h.get("procedure_codes"),
        # 고침 4(S08 T3) — 매핑 제품(허브 제품 이름). KOS 약 칸(`medicine`)과 매핑 제품이 다를 수 있다(「턱보톡스(국산)」 = 약 칸 보톡스 ·
        # 매핑 제품 하이톡스) — 제품으로 고른 주제에서 그 줄이 같은 제품 줄인지 답 모델이 데이터로 본다
        "products": [p.get("name") for p in h.get("products") or [] if isinstance(p, dict) and p.get("name")],
        "options": opts,
    }
    # 고침 2(검수 P1-1) — `note` 는 데이터 작업 메모(「구성1 확인 필요」 · 「KOS 기기 칸」)라 답 모델에 싣지 않는다(모달 · 장부는 그대로)
    if len(keys) > OPTIONS_MAX:
        out["options_more"] = len(keys) - OPTIONS_MAX
    return out


def cart_pairs(kos: KosMap, cart: list[dict]) -> list[tuple[str, str]]:
    """LC9 — 장바구니 진료항목들의 허브 시술 쌍(장바구니 순서 · 같은 시술이 두 진료항목에 있으면 a = b 도 · 한도 10)."""
    procs: list[str] = []
    for e in cart:
        procs += [p for p in kos.item.get(e["code"], {}).get("hub", {}).get("procedure_codes") or []]
    out: list[tuple[str, str]] = []
    for i in range(len(procs)):
        for j in range(i + 1, len(procs)):
            a, b = procs[i], procs[j]
            key = (a, b) if a <= b else (b, a)
            if key not in out:
                out.append(key)
    return out


def build(
    kos: KosMap,
    prompt_kos: dict,
    *,
    cond: dict,
    items: list[str],
    cart: list[dict],
    cart_rec: dict | None,
    viewed: list[str],
    pairs: dict[tuple[str, str], list[dict]],
    pairs_capped: bool,
    course: list[dict],
    figs: list[dict],
    sent: dict,
    items_max: int,
    force_cart: bool = False,
    force_items: bool = False,
    absent: list[dict] | None = None,
) -> tuple[str, dict]:
    """→ (블록 글, 장부 `llm.kos_block{codes, lines, chars, cart_sig, pairs, …}`)."""
    lb = prompt_kos["labels"]
    lines = [prompt_kos["block_header"]]
    if absent:  # 고침 3(r3 P2-4 · H10) — 손님이 댄 이름 · 명동점 진료항목 0(매핑으로 확인)을 블록 맨 앞 사실 줄로
        lines.append(f"{lb['absent']} {_j(absent)}")
    lines.append(f"{lb['conditions']} {_j(cond)}")
    want: list[str] = []
    for c in items[:items_max] + [e["code"] for e in cart] + list(viewed):
        if c in kos.item and c not in want:
            want.append(c)
    new = [c for c in want if c not in sent["codes"] or (force_items and c in items[:items_max])]  # 고침 2 — 가격 물음 턴
    for c in new:
        lines.append(f"{lb['items']} {_j(item_line(kos, c))}")
    over = max(0, len(items) - items_max)
    if over:
        lines.append(f"{prompt_kos['overflow']} {over}")
    cart_view = [
        {
            "code": e["code"],
            "name": kos.item[e["code"]].get("kos_item_name"),
            "lines": [
                {
                    "product": kos.option_product[tuple(k)].get("title"),
                    "title": kos.option[tuple(k)].get("title"),
                    "count": kos.option[tuple(k)].get("count"),
                    "price_amount": kos.option[tuple(k)].get("price_amount"),
                    "event_price": kos.option[tuple(k)].get("event_price"),
                }
                for k in e.get("lines") or []
                if tuple(k) in kos.option
            ],
        }
        for e in cart
        if e["code"] in kos.item
    ]
    rec_view = None
    if cart_rec is not None:
        rec_view = {
            "cards": [
                {
                    "total": c["total"],
                    "list_total": c["list_total"],
                    "options": [
                        {
                            "key": o["key"],
                            "product": o["product"],
                            "title": o["title"],
                            "used": o["used"],
                            "list": o["list"],
                            "covers": o["covers"],
                        }
                        for o in c["options"]
                    ],
                }
                for c in cart_rec["cards"]
            ],
            "no_pick": cart_rec.get("no_pick_names"),
            "no_price": cart_rec.get("no_price_names"),
            "capped": cart_rec.get("capped"),
        }
    cart_sig = hashlib.sha256(_j([cart_view, rec_view]).encode()).hexdigest()[:16]
    if cart_sig != sent["cart_sig"] or force_cart:  # LC5 — 장바구니가 바뀐 턴만(같이 받기 질문 턴은 예외 — S-2)
        lines.append(f"{lb['cart']} {_j(cart_view)}")
        if rec_view is not None:
            lines.append(f"{lb['rec']} {_j(rec_view)}")
    sent_pairs = []
    for (a, b), rows in pairs.items():
        key = f"{a}|{b}"
        if key in sent["pairs"] and not force_cart:
            continue
        sent_pairs.append(key)
        lines.append(f"{lb['pairs']} {_j({'a': a, 'b': b, 'rows': [{k: r.get(k) for k in PAIR_KEEP} for r in rows]})}")
    for c in course:
        lines.append(f"{lb['course']} {_j(c)}")
    if figs:
        lines.append(f"{lb['figs']} {_j(figs)}")
    text = "\n".join(lines)
    meta = {
        "force_cart": force_cart,
        "force_items": force_items,
        "absent": list(absent or []),
        "codes": new,
        "lines": len(lines),
        "chars": len(text),
        "cart_sig": cart_sig,
        "pairs": sent_pairs,
        "pairs_capped": pairs_capped,
        "overflow": over,
        "figs": [f["code"] for f in figs],
    }
    return text, meta


def cond_view(dec, items_rec: dict | None, axis: dict | None, topics_brief: list[dict], items_max: int = 30) -> dict:
    """LC7 — 조건 줄(주제 · 칸 · 바뀐 칸 · 막은 조건 · 국가로 뺀 수 · 다음 질문 축)."""
    rec = (dec.rec if dec is not None else {}) or {}
    ir = items_rec or {}
    conds = ir.get("conditions") or {}
    out = {
        "topic": dec.active if dec is not None else None,
        "list_n": len(ir.get("codes") or []),
        "list_codes": list(ir.get("codes") or [])[
            :items_max
        ],  # 지금 목록(진료항목 줄은 앞 턴에 보냈으면 다시 안 보냄 — LC5)
        "conditions": {
            k: [{"name": v.get("name"), "code": v.get("code")} for v in conds.get(k) or []]
            for k in ("procedure", "site", "concern", "effect", "product")
            if conds.get(k)
        },
        "country": conds.get("country"),
        "prev_topics": topics_brief,
    }
    tc = ir.get("turn_conditions") or {}
    out["changed"] = [k for k in ("procedure", "site", "concern", "effect", "product") if tc.get(k)] + (
        ["country"] if tc.get("country") else []
    )
    if rec.get("change"):
        out["topic_change"] = {"to": dec.active, "by": rec.get("by"), "conditions": cond_names(conds)}
    out["slots"] = cond_tuple(conds)  # 지금 주제 칸 묶음(주제 튜플 11-22)
    if rec.get("move"):
        out["topic_move"] = rec["move"]  # 주제 튜플(11-22) — same · deepen · return · ask · new · first
    for k in ("undo", "widen"):  # 11-23 바로잡기 · 넓히기(그 턴에 일어났으면)
        if rec.get(k):
            out[k] = rec[k]
    if rec.get("pick_zero"):
        out["pick_zero"] = True  # 11-23 시술 · 제품 교체로 0 — 막지 않음 · 봇이 0 이라고 말함
    for k in ("list_held", "out_pick", "asked_pick", "suspended"):  # SPEC-012 HL1 · HQ2 · HQ1 · HP1 표지(문구 없음)
        if rec.get(k):
            out[k] = rec[k]
    if ir.get("ask_topics"):
        brief = {b["id"]: b for b in topics_brief}
        out["ask_topic"] = [{"id": i, "slots": (brief.get(i) or {}).get("slots")} for i in ir["ask_topics"]]
    if ir.get("excluded"):
        out["excluded"] = ir["excluded"]
    if ir.get("blocked"):
        out["blocked"] = ir["blocked"]
    if ir.get("country_dropped"):
        out["country_dropped"] = ir["country_dropped"]
    if ir.get("hub_error"):
        out["hub_error"] = True
    if axis and axis.get("axis"):
        out["ask"] = {"axis": axis["axis"], "options": [o["name"] for o in axis["options"]]}
    return out
