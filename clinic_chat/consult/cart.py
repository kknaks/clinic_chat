"""장바구니(SPEC-010 §4-6 CT1 ~ CT8 · OP1 ~ OP8 · 결정 D-15) — 대화마다 서버가 든다(메모리 + 장부 `cart` 줄).

- 담는 단위 = 진료항목(OP1 · 옵션 0 줄) · 옵션 줄 = 그 진료항목을 덮는 single(판매 종료 빼고 · OP3) · 줄 여러 개 · 같은 옵션 두 줄 가능(OP4).
- 진료항목을 빼면 그 줄도 함께(OP7) · 「모두 비우기」 = 전부 · 목록 · 조건 · 주제와 별개(CT8 · TP8).
- 장부에는 진료항목 코드 · 옵션 키만(가격 숫자 0 · JS6).
"""

from __future__ import annotations

import copy

from clinic_chat.consult import price as pr
from clinic_chat.consult.kos_map import KosMap

OPS = ("add", "del", "clear", "line_add", "line_del")


class CartError(ValueError):
    pass


def apply(
    kos: KosMap,
    cart: list[dict],
    op: str,
    *,
    code: str = "",
    product_key: str = "",
    option_key: str = "",
    idx: int | None = None,
) -> list[dict]:
    """한 동작 → 새 장바구니(입력은 안 바꿈). 받을 수 없는 값이면 `CartError`."""
    out = copy.deepcopy(cart)
    if op not in OPS:
        raise CartError("op")
    if op == "clear":
        return []
    if code not in kos.item:  # RT1 — `$J` 에 정확히 있을 때만
        raise CartError("code")
    have = next((e for e in out if e["code"] == code), None)
    if op == "add":
        if have is None:
            out.append({"code": code, "lines": []})
        return out
    if op == "del":
        return [e for e in out if e["code"] != code]
    if have is None:
        raise CartError("not_in_cart")
    if op == "line_add":
        k = (product_key, option_key)
        if k not in pr.single_options(kos, code):  # OP3 — 그 진료항목의 single · 판매 종료 뺌
            raise CartError("option")
        have["lines"].append([product_key, option_key])
        return out
    if idx is None or not 0 <= idx < len(have["lines"]):
        raise CartError("idx")
    del have["lines"][idx]
    return out


def view(kos: KosMap, cart: list[dict]) -> list[dict]:
    """드로어 진료항목 줄 값(CT2 + 옵션 줄 · 드롭다운)."""
    from clinic_chat.consult.items_view import cart_item

    out = []
    for e in cart:
        ci = cart_item(kos, e["code"])
        if ci is None:
            continue
        lines = []
        for i, (pk, ok) in enumerate(e.get("lines") or []):
            k = (pk, ok)
            if k not in kos.option:
                continue
            o = kos.option[k]
            lines.append(
                {
                    "idx": i,
                    "product_key": pk,
                    "option_key": ok,
                    "title": o.get("title") or "",
                    "price_amount": o.get("price_amount"),
                    "event_price": o.get("event_price"),
                    "closed": pr.closed(kos, k),
                }
            )
        out.append({**ci, "lines": lines})
    return out


def ledger_view(cart: list[dict]) -> list[dict]:
    """장부 `cart` 줄 꼴 — `[{code, lines: [[product_key, option_key]]}]`(가격 0)."""
    return [{"code": e["code"], "lines": [list(x) for x in e.get("lines") or []]} for e in cart]


def rec_view(kos: KosMap, cart: list[dict], *, cards: int = pr.CARDS) -> dict:
    """§6-3 — 추천 카드 + 「옵션 안 고름」 · 「가격 없음」 이름(진료항목 이름 원문)."""
    r = pr.recommend(kos, cart, cards=cards)
    r["no_pick_names"] = [kos.item[c].get("kos_item_name") or c for c in r["no_pick"]]
    r["no_price_names"] = [kos.item[c].get("kos_item_name") or c for c in r["no_price"]]
    return r


def rec_ledger(r: dict) -> dict:
    """장부 `rec` 칸 — 옵션 키 · 표지만(가격 0 · JS6)."""
    return {
        "cards": [c["keys"] for c in r["cards"]],
        "no_pick": r["no_pick"],
        "no_price": r["no_price"],
        "closed_lines": r["closed_lines"],
        "filtered": r["filtered"],
        "capped": r["capped"],
        "combos": r["combos"],
    }
