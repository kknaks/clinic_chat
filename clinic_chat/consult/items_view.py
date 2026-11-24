"""왼쪽 화면 값 — 진료항목 접기 · 카드 · 얼굴 점 · 몸 목록(SPEC-010 §4-2 FS1 ~ FS5 · §4-3 IL1 ~ IL10 · §4-7).

값은 `$J`(kos_map) 원문만 쓴다. 문구는 짓지 않는다 — 상태 표지는 `data-status` + 빈 `data-slot` 자리(§12).
입력 `codes` = 이번 목록의 진료항목 코드(감지 순). 묶음 순서 = 묶음의 첫 진료항목이 `codes` 에 처음 나온 순(IL2) ·
묶음 안 순서 = `groups[].items` 순서. 첫 묶음만 펼침(IL3).
"""

from __future__ import annotations

from clinic_chat.consult import modal_text as mt
from clinic_chat.consult.kos_map import KosMap

W, H = 590, 1060  # 얼굴 그림 viewBox(design-face-0.svg)
NONE = "\ud574\ub2f9 \uc5c6\uc74c"  # `$J` 원문 값 「해당 없음」(앱 문장 아님)
MAPPED = "\ub9e4\ud551\ub428"  # status 원문 「매핑됨」
STATUS_SLOT = {  # §4-7 · §12 — status 원문 → 표지 slot 이름
    "\ub9e4\ud551\ub428(\uc81c\ud488 \ud655\uc778 \ud544\uc694)": "status-product-check",  # 매핑됨(제품 확인 필요)
    "\uc2dc\uc220 \ud655\uc778 \ud544\uc694": "status-procedure-check",  # 시술 확인 필요
    "\ub9e4\ud551\ub428(\ud655\uc778 \ud544\uc694: BBL \ud544\ud130)": "status-bbl-filter",  # 매핑됨(확인 필요: BBL 필터)
    "\ud5c8\ube0c \ub300\uc0c1 \uc544\ub2d8": "status-not-hub",  # 허브 대상 아님
}


def site_label(it: dict) -> str:
    """IL4 `data-site` — `site_tiers` 의 가장 아래 빈칸 아닌 단(t3 → t2 → t1)."""
    t = it.get("site_tiers") or {}
    return next((t[k] for k in ("t3", "t2", "t1") if t.get(k)), "")


def drug_chip(it: dict) -> str:
    """IL6 · U-23 — 약물 원문 · 「해당 없음」 이면 기기 · 둘 다면 빈 값(칩 숨김)."""
    for k in ("kos_medicine", "kos_machine"):
        v = (it.get(k) or "").strip()
        if v and v != NONE:
            return v
    return ""


def card(it: dict, cart: set[str] | frozenset[str] = frozenset(), desc: str = "") -> dict:
    st = it.get("status") or ""
    return {
        "code": it["kos_item_code"],
        "name": it.get("kos_item_name") or "",
        "unit": it.get("kos_unit") or "",  # 테스트 고침 B — 용량 원문(같은 이름 진료항목이 용량으로 갈려 보임)
        "desc": desc,  # 테스트 고침 B — 설명(kos_description · 비면 케이스 한 줄 — modal_text.desc_for)
        "kind": it.get("kos_procedure") or "",
        "site": site_label(it),
        "device": it.get("kos_machine") or "",
        "drug": drug_chip(it),
        "status": st if st != MAPPED else "",
        "status_slot": STATUS_SLOT.get(st, "") if st != MAPPED else "",
        "published": it.get("kos_status") == "PUBLISHED",
        "in_cart": it["kos_item_code"] in cart,
    }


def belongs(kos: KosMap, code: str, site: str) -> bool:
    """테스트 고침 A-2(사용자 11-22) — 진료항목의 매핑 부위(`hub.site_codes` = 할 수 있는 범위)가 점 부위를 품는가(같음 · 조상).
    얼굴 전체 진료항목은 얼굴 점 모두에 · 전신 진료항목은 몸 점 모두에 속한다. 부위가 `sites` 에 없으면 거짓."""
    if code not in kos.item or site not in kos.sites:
        return False
    cover = {site, *kos.ancestors(site)}
    return bool(cover & set(kos.item[code]["hub"].get("site_codes") or []))


def filter_site(kos: KosMap, codes: list[str], site: str | None) -> list[str]:
    """A-3 — 점 누름 = 그 점에 속하는 진료항목만(`belongs`). 화면 동작만(조건 · 주제 · 장부 줄 0)."""
    if not site:
        return list(codes)
    return [c for c in codes if belongs(kos, c, site)]


def dots_of(kos: KosMap, sites: list[str] | None) -> tuple[list[dict], list[dict]]:
    """A-1 — 점 = 대화에서 나온 부위(주제 `sites` · 쌓인 순 · 같은 부위 한 번) · 얼굴 = 점 · 몸 = 그림 아래 목록."""
    dots: list[dict] = []
    body: list[dict] = []
    for s in dict.fromkeys(sites or []):
        v = kos.sites.get(s)
        if v is None:
            continue
        if v.get("region") == "face":
            pts = [[round(p[0] * W, 1), round(p[1] * H, 1)] for p in v.get("points") or []]
            dots.append({"site_code": s, "name": v.get("name") or "", "points": pts})
        else:
            body.append({"site_code": s, "name": v.get("name") or ""})
    return dots, body


def view(
    kos: KosMap,
    codes: list[str],
    *,
    cart: set[str] | frozenset[str] = frozenset(),
    site: str | None = None,
    sites: list[str] | None = None,
) -> dict:
    """`items` 조각 값 — kinds(접기) · 합계 · 얼굴 점 · 몸 목록. 없는 코드는 버린다.
    점 · 몸 목록 = `sites`(대화 부위 — A-1) · 진료항목 부위를 모아 찍지 않는다. 카드 강조 자리 = 그 진료항목이 속한 점(A-5)."""
    seen: list[str] = []
    for c in filter_site(kos, codes, site):
        if c in kos.item and c not in seen:
            seen.append(c)
    in_list = set(seen)
    kinds: list[dict] = []
    kind_of: dict[str, dict] = {}
    for c in seen:
        name = kos.item[c].get("kos_procedure") or ""
        if name not in kind_of:
            k = {
                "name": name,
                "cards": [
                    card(kos.item[x], cart, mt.desc_for(kos, x))
                    for x in kos.by_kos_procedure.get(name, ())
                    if x in in_list
                ],
            }
            kind_of[name] = k
            kinds.append(k)
    dots, body = dots_of(kos, sites)
    for k in kinds:  # A-5 — 카드 「시술설명」 강조 자리(data-points · data-body-sites) = 그 진료항목이 속한 점
        for cd in k["cards"]:
            cs = card_sites(kos, cd["code"], sites)
            cd["points"] = ";".join(f"{x},{y}" for x, y in cs["face"])
            cd["body_sites"] = ",".join(cs["body"])
    return {
        "kinds": kinds,
        "n_kinds": len(kinds),
        "n_items": sum(len(k["cards"]) for k in kinds),
        "dots": dots,
        "body": body,
        "site": site or "",
    }


def card_sites(kos: KosMap, code: str, sites: list[str] | None = None) -> dict:
    """A-5 — 카드 강조 자리 = 대화 부위 가운데 그 진료항목이 속한 점(`belongs`)의 얼굴 좌표 · 몸 부위 코드."""
    face, body = [], []
    for s in dict.fromkeys(sites or []):
        if not belongs(kos, code, s):
            continue
        v = kos.sites[s]
        if v.get("region") == "face":
            face += [[round(p[0] * W, 1), round(p[1] * H, 1)] for p in v.get("points") or []]
        else:
            body.append(s)
    return {"face": face, "body": body}


def cart_item(kos: KosMap, code: str) -> dict | None:
    """CT2 · OP3 — 장바구니 진료항목 줄 값 + 드롭다운 선택지(그 진료항목을 덮는 single · 판매 종료 뺌 · `option_refs` 순서 ·
    원본 제목 · 정가 · 이벤트가). 없는 코드면 None(RT1 — 정확히 있을 때만)."""
    it = kos.item.get(code)
    if it is None:
        return None
    c = card(it)
    opts = []
    for k in kos.covers_of.get(code, ()):
        o, p = kos.option[k], kos.option_product[k]
        if o.get("type") != "single" or p.get("closed") or o.get("closed"):
            continue
        opts.append(
            {
                "product_key": k[0],
                "option_key": k[1],
                "title": o.get("title") or "",
                "count": o.get("count"),
                "price_amount": o.get("price_amount"),
                "event_price": o.get("event_price"),
            }
        )
    return {**c, "options": opts}


COND_ORDER = (
    "site",
    "concern",
    "procedure",
    "effect",
    "product",
)  # 테스트 고침 E — 머리 순서 = 부위 → 고민 → 시술 → 효과 → 제품 → 국가(예 「얼굴 · 피부 탄력 저하 · 소프웨이브 · 써마지」)


def cond_tuple(conditions: dict | None) -> dict:
    """주제 튜플(11-22) — 칸마다 원문 이름(빈 칸은 뺌) · 국가 · 뺌. 이해 · 답 호출 블록에 그대로."""
    c = conditions or {}
    out: dict = {k: [str(v.get("name") or v.get("code") or "") for v in c.get(k) or []] for k in COND_ORDER}
    out = {k: v for k, v in out.items() if v}
    if c.get("country"):
        out["country"] = c["country"]
    if c.get("exclude"):
        out["exclude"] = [str(v.get("name") or v.get("code") or "") for v in c["exclude"]]
    return out


def cond_names(conditions: dict | None) -> list[str]:
    """IL11 머리 글 — 주제 조건 칸 값의 원문 이름을 칸 순서대로(국가는 원문 값 · 잇는 글자 0)."""
    c = conditions or {}
    out = [
        str(v.get("name") or v.get("code") or "")
        for k in COND_ORDER
        for v in c.get(k) or []
        if v.get("from") != "product"  # 테스트 고침 E — 제품에서 풀린 시술은 거르기에만(머리에 안 보임)
    ]
    if c.get("country"):
        out.append(str(c["country"]))
    return list(dict.fromkeys(x for x in out if x))  # 같은 이름 한 번(제품 이름 · 이름 일치가 같을 때)


def topics_view(
    kos: KosMap, topics: list[dict], active: str | None, *, site: str | None = None, max_n: int = 6
) -> dict:
    """§4-3 IL11 · TP9 · TP11 — 지금 주제(맨 위 · 펼침 · 안 = `view`) + 앞 주제(만든 차례 역순 · 접힘 머리만) · 보이는 수
    `max_n`(지금 + 앞 max_n - 1) · 주제가 하나면 머리 없음(`show_topics` False). 점 · 몸 목록은 지금 주제의 대화 부위(`sites` — A-1)."""
    by_id = {str(t.get("id")): t for t in topics or []}
    cur = by_id.get(str(active)) if active is not None else None
    if cur is None and topics:
        cur = topics[-1]
    v = view(kos, list((cur or {}).get("items") or []), site=site, sites=list((cur or {}).get("sites") or []))
    prev = sorted((t for t in topics or [] if t is not cur), key=lambda t: t.get("created_turn") or 0, reverse=True)
    heads = [cur, *prev][: max(1, max_n)] if cur is not None else []
    v["topics"] = [
        {
            "id": str(t.get("id")),
            "current": t is cur,
            "conds": cond_names(t.get("conditions")),
            "n": len(t.get("items") or []),
        }
        for t in heads
    ]
    v["show_topics"] = len(v["topics"]) >= 1  # 테스트 고침 E(11-23) — 주제가 하나여도 머리를 보인다
    # 테스트 고침 D(사용자 11-23) — 얼굴 점은 늘 다 뜬다: 점 = 대화 전체(모든 주제)의 부위 · 지금(보이는) 주제 점만 강조
    # (`current`) · 점마다 그 부위를 가진 주제(`topic` — 지금 주제가 가지면 지금 주제 · 아니면 가장 최근 앞 주제)
    owner: dict[str, str] = {}
    order: list[str] = []
    for t in [*(sorted(topics or [], key=lambda t: t.get("created_turn") or 0))]:
        for s_ in t.get("sites") or []:
            if s_ not in order:
                order.append(s_)
            owner[s_] = str(t.get("id"))
    for s_ in (cur or {}).get("sites") or []:
        owner[s_] = str((cur or {}).get("id"))
    dots, body = dots_of(kos, order)
    mine = set((cur or {}).get("sites") or [])
    for d in [*dots, *body]:
        d["topic"] = owner.get(d["site_code"], "")
        d["current"] = d["site_code"] in mine
    v["dots"], v["body"] = dots, body
    return v


def site_owner(topics: list[dict], active: str | None, site: str) -> str | None:
    """점 누름 — 그 부위를 가진 주제(지금 주제가 가지면 지금 주제 · 아니면 가장 최근 앞 주제 · 없으면 None)."""
    cur = next((t for t in topics or [] if str(t.get("id")) == str(active)), None)
    if cur is not None and site in (cur.get("sites") or []):
        return str(cur["id"])
    have = [t for t in topics or [] if site in (t.get("sites") or [])]
    return str(max(have, key=lambda t: t.get("created_turn") or 0)["id"]) if have else None


def face_sites(kos: KosMap) -> list[dict]:
    """FS6 `/face-check` — `$J` 얼굴 부위 전부(코드 · 좌표 px)."""
    return [
        {"site_code": k, "points": [[round(p[0] * W, 1), round(p[1] * H, 1)] for p in v.get("points") or []]}
        for k, v in kos.sites.items()
        if v.get("region") == "face"
    ]
