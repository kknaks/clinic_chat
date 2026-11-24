"""최저가 조합(SPEC-010 §6-2 RC1 ~ RC10 · CO1 ~ CO4 · D-16 · D-17) — 순수 함수(입력 = 장바구니 · `$J` · 출력 = 카드 · 표지).

- 단위 = 옵션 줄(진료항목 c · 고른 옵션의 count n). 후보 = ① 고른 옵션 자체 ② c 를 덮고 count 가 같은 다른 single
  (판매 종료 · CO1 0 원 · CO2 회원권 빼고 · 상품 제목 남성 / 여성 낱말이 같을 때만) ③ group(구성 (진료항목, count)이 전부
  장바구니 옵션 줄에 있고 장바구니 밖 진료항목을 품지 않을 때 · 판매 종료 빼고).
- 조합 = 줄 전부를 겹침 없이 덮는 묶음(집합 덮기) · 합계 = 쓰인 가격(이벤트가 있으면 이벤트가 · 없으면 정가) 더하기 ·
  group 구성 줄의 이벤트가는 안 씀(옵션 단위 값만) · 카드 = 최저 · 두 번째 최저(옵션 키 묶음이 다른 것).
- 가격 숫자는 이 결과(화면 · LLM)로만 나간다 — 장부에는 옵션 키만(JS6).
"""

from __future__ import annotations

from dataclasses import dataclass

from clinic_chat.consult.kos_map import KosMap

OptKey = tuple[str, str]
CO2_WORDS = ("1\ub144\uad8c", "\uac00\uc785\ube44")  # 「1년권」 · 「가입비」
CO3_WORD = "[\uccab\ubc29\ubb38]"  # 「[첫방문]」
CO3_CATEGORY = "10\uc6d4 \uccab\ubc29\ubb38 \uc774\ubca4\ud2b8"  # 「10월 첫방문 이벤트」
GENDER = ("\ub0a8\uc131", "\uc5ec\uc131")  # 「남성」 · 「여성」
LINES_MAX = 10  # RC9
COMBOS_MAX = 10_000
CARDS = 2


def used(o: dict) -> int:
    """RC4 — 옵션 단위 이벤트가가 있으면 그것 · 없으면 정가."""
    ev = o.get("event_price")
    return int(ev if ev is not None else (o.get("price_amount") or 0))


def closed(kos: KosMap, k: OptKey) -> bool:
    return bool(kos.option_product[k].get("closed") or kos.option[k].get("closed"))


def co_rules(kos: KosMap, k: OptKey) -> list[str]:
    """CO1 ~ CO3 — 원문 규칙(규칙마다 따로)."""
    p, o = kos.option_product[k], kos.option[k]
    out = []
    if used(o) == 0:
        out.append("CO1")
    if any(w in (p.get("title") or "") for w in CO2_WORDS):
        out.append("CO2")
    cats = {c.get("category_key"): c.get("name") for c in kos.product_categories}
    if (
        CO3_WORD in (p.get("title") or "")
        or CO3_WORD in (o.get("title") or "")
        or any(cats.get(c) == CO3_CATEGORY for c in p.get("category_keys") or [])
    ):
        out.append("CO3")
    return out


def gender(kos: KosMap, k: OptKey) -> str | None:
    t = kos.option_product[k].get("title") or ""
    hits = [g for g in GENDER if g in t]
    return hits[0] if len(hits) == 1 else ("+".join(hits) if hits else None)


def single_options(kos: KosMap, code: str) -> list[OptKey]:
    """OP3 — 드롭다운 선택지(그 진료항목을 덮는 single · 판매 종료 뺌 · `option_refs` 순서)."""
    return [k for k in kos.covers_of.get(code, ()) if kos.option[k].get("type") == "single" and not closed(kos, k)]


@dataclass
class Line:
    idx: int
    code: str
    count: int | None
    chosen: OptKey


def _order(kos: KosMap) -> dict[OptKey, int]:
    return {k: i for i, k in enumerate(kos.option)}


def recommend(
    kos: KosMap, cart: list[dict], *, lines_max: int = LINES_MAX, combos_max: int = COMBOS_MAX, cards: int = CARDS
) -> dict:
    """RC1 ~ RC10. `cart` = [{code, lines: [[product_key, option_key], …]}](장바구니 순서)."""
    out: dict = {
        "cards": [],
        "no_pick": [],
        "no_price": [],
        "closed_lines": [],
        "filtered": {"CO1": 0, "CO2": 0, "CO3": 0},
        "capped": False,
        "combos": 0,
        "lines": 0,
    }
    codes = [e["code"] for e in cart if e.get("code") in kos.item]
    lines: list[Line] = []
    for e in cart:
        c = e.get("code")
        if c not in kos.item:
            continue
        if not single_options(kos, c):  # RC6 — 덮는 single 0(옵션 0 · group 만)
            out["no_price"].append(c)
            continue
        got = 0
        for pk, ok in e.get("lines") or []:
            k = (pk, ok)
            if (
                k not in kos.option or k not in kos.covers_of.get(c, ()) or closed(kos, k)
            ):  # RC10 · 그 진료항목을 안 덮으면 받지 않음 — 고른 옵션이 판매 종료 → 그 줄은 옵션 안 고름
                out["closed_lines"].append({"code": c, "key": list(k)})
                continue
            lines.append(Line(len(lines), c, kos.option[k].get("count"), k))
            got += 1
        if not got:
            out["no_pick"].append(c)  # OP6
    out["lines"] = len(lines)
    if not lines:
        return out
    order = _order(kos)
    if len(lines) > lines_max:  # RC9
        out["capped"] = True
        out["cards"] = [_card(kos, [(ln.chosen, [ln.idx]) for ln in lines], lines)]
        return out
    in_cart = set(codes)
    # RC2 ① ② — 줄마다 single 후보
    filtered: dict[str, set[OptKey]] = {"CO1": set(), "CO2": set(), "CO3": set()}
    singles: dict[int, list[OptKey]] = {}
    for ln in lines:
        cands = [ln.chosen]
        g0 = gender(kos, ln.chosen)
        for k in kos.covers_of.get(ln.code, ()):
            o = kos.option[k]
            if k == ln.chosen or o.get("type") != "single" or closed(kos, k) or o.get("count") != ln.count:
                continue
            rules = co_rules(kos, k)
            for r in rules:
                filtered[r].add(k)
            if "CO1" in rules or "CO2" in rules:
                continue
            if gender(kos, k) != g0:  # (나) 남성 / 여성 낱말이 같을 때만(둘 다 없으면 같음)
                continue
            cands.append(k)
        singles[ln.idx] = cands
    out["filtered"] = {r: len(v) for r, v in filtered.items()}
    # RC2 ③ — group 후보: 구성 (진료항목, count) 전부가 장바구니 옵션 줄에 · 장바구니 밖 진료항목 0
    groups: list[tuple[OptKey, list[tuple[str, int | None]]]] = []
    seen_g: set[OptKey] = set()
    for c in codes:
        for k in kos.covers_of.get(c, ()):
            o = kos.option[k]
            if o.get("type") != "group" or k in seen_g or closed(kos, k):
                continue
            seen_g.add(k)
            if not set(o.get("covers") or []) <= in_cart:
                continue
            need = [(x.get("item_code"), x.get("count")) for x in o.get("compositions") or []]
            if need and all(any(ln.code == ic and ln.count == n for ln in lines) for ic, n in need):
                groups.append((k, need))
    # RC3 — 집합 덮기(겹침 0 · 같은 (c, n) 줄 둘이면 둘 다)
    combos: list[list[tuple[OptKey, list[int]]]] = []

    def assign(need: list[tuple[str, int | None]], free: set[int], first: int) -> list[int] | None:
        got: list[int] = []
        pool = set(free)
        for i, (ic, n) in enumerate(need):
            cand = sorted(j for j in pool if lines[j].code == ic and lines[j].count == n)
            if i == 0:
                cand = [first] if first in cand else []
            if not cand:
                return None
            got.append(cand[0])
            pool.discard(cand[0])
        return got

    def walk(free: set[int], acc: list[tuple[OptKey, list[int]]]) -> None:
        if len(combos) > combos_max:
            return
        if not free:
            combos.append(list(acc))
            return
        j = min(free)
        for k in singles[j]:
            acc.append((k, [j]))
            walk(free - {j}, acc)
            acc.pop()
        for k, need in groups:
            rot = next((r for r in range(len(need)) if need[r] == (lines[j].code, lines[j].count)), None)
            if rot is None:
                continue
            got = assign([need[rot]] + need[:rot] + need[rot + 1 :], free, j)
            if got is None:
                continue
            acc.append((k, got))
            walk(free - set(got), acc)
            acc.pop()

    walk({ln.idx for ln in lines}, [])
    out["combos"] = len(combos)
    if len(combos) > combos_max:  # RC9
        out["capped"] = True
        out["cards"] = [_card(kos, [(ln.chosen, [ln.idx]) for ln in lines], lines)]
        return out
    scored = []
    for cb in combos:
        keys = tuple(sorted((k for k, _ in cb), key=lambda k: order[k]))
        total = sum(used(kos.option[k]) for k, _ in cb)
        scored.append((total, len(cb), tuple(order[k] for k in keys), keys, cb))
    scored.sort(key=lambda x: x[:3])  # RC7 — 합계 → 옵션 수 → `$J` 순서
    seen: set = set()
    for _total, _, _, keys, cb in scored:
        if keys in seen:
            continue
        seen.add(keys)
        out["cards"].append(_card(kos, cb, lines))
        if len(out["cards"]) >= cards:
            break
    return out


def _card(kos: KosMap, cb: list[tuple[OptKey, list[int]]], lines: list[Line]) -> dict:
    """§6-3 — 옵션마다 한 줄(장바구니 순서대로 처음 덮은 옵션부터) · 합계 · 정가 합계 · 이벤트 표지 · group 이 덮은 이름."""
    rows = []
    for k, idx in sorted(cb, key=lambda x: min(x[1])):
        p, o = kos.option_product[k], kos.option[k]
        rows.append(
            {
                "key": list(k),
                "product": p.get("title") or "",
                "title": o.get("title") or "",
                "used": used(o),
                "list": int(o.get("price_amount") or 0),
                "type": o.get("type"),
                "event": o.get("event_price") is not None or bool(p.get("is_event")),
                "first_visit": "CO3" in co_rules(kos, k),
                "covers": [kos.item[lines[i].code].get("kos_item_name") or "" for i in idx]
                if o.get("type") == "group"
                else [],
            }
        )
    total = sum(r["used"] for r in rows)
    list_total = sum(r["list"] for r in rows)
    return {
        "total": total,
        "list_total": list_total,
        "event": any(r["event"] or r["first_visit"] for r in rows),
        "options": rows,
        "keys": [r["key"] for r in rows],
    }


def co_counts(kos: KosMap) -> dict:
    """리포트 — `$J` 전체에서 규칙마다 따로 센 옵션 수 · 겹침 · 합집합(실측 기대 CO1 13 · CO2 22 · CO3 23 · 겹침 10 · 25)."""
    s = {r: {k for k in kos.option if r in co_rules(kos, k)} for r in ("CO1", "CO2", "CO3")}
    return {
        **{r: len(v) for r, v in s.items()},
        "CO1&CO2": len(s["CO1"] & s["CO2"]),
        "drop_union": len(s["CO1"] | s["CO2"]),
    }
