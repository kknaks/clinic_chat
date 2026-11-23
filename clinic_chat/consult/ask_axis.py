"""다음 질문 축(SPEC-010 §5-4 AX1 ~ AX7 · 결정 D-14) — 지금 주제 목록 L 을 가장 잘 가르는 축 하나와 선택지.

- `pick` 은 순수 함수다(입력 = L · 축 값 표 · 칸 상태 → 출력 = 축 · 선택지 · 점수 — AX7 시험 대상).
- `axis_values` 는 허브로 축 값 표를 짓는다(AX3 · AX6) — 고민 · 효과는 케이스마다 `facets`(프로세스 캐시) · 한 턴 새 호출이
  한도(40)를 넘으면 그 두 축은 이번 턴에서 빼고 `skipped_axes` 표지.
- 회차 · 대상 축(AX2 — 장바구니에서 옵션 줄 없는 진료항목)은 장바구니가 서는 WP-5 에서 값이 들어온다(지금은 인자만).
"""

from __future__ import annotations

from clinic_chat.consult.kos_map import KosMap

AXES = ("site", "concern", "effect", "product", "target", "count")  # AX4 동점 순서(진료항목 축 먼저 · 옵션 축 뒤)
ITEM_AXES = ("site", "concern", "effect", "product")
OPTIONS_MAX = 5
CALL_MAX = 40  # AX6
SELF_ROLE = "\uc790\uae30"  # 허브 role 값 「자기」
CASE_DIRECT = "\ucf00\uc774\uc2a4 \uc9c1\uc811"  # 허브 reach 값 「케이스 직접」
UPPER_PRODUCT = "\uc0c1\uc704 \uc81c\ud488"  # 허브 scope role 값 「상위 제품」

_CACHE: dict[tuple[str, str, str], list] = {}  # (gold_hash, 노드, facet|scope) → 행


def pick(
    L: list[str],
    values: dict[str, dict[str, list[str]]],
    *,
    filled: set[str] | frozenset[str] = frozenset(),
    order: dict[str, int] | None = None,
    names: dict[tuple[str, str], str] | None = None,
    min_list: int = 4,
    skipped: list[str] | None = None,
) -> dict:
    """AX1 · AX2 · AX4 · AX5 — `values[축][진료항목] = [값]`(값 없음 = 빈 목록 → 「없음」 묶음)."""
    order = order or {}
    names = names or {}
    out: dict = {"axis": None, "options": [], "scores": {}, "excluded": {}, "skipped_axes": list(skipped or [])}
    if len(L) <= min_list:  # AX1
        out["excluded"]["*"] = "list_small"
        return out
    best: tuple | None = None
    groups_of: dict[str, dict[str, list[str]]] = {}
    for ax in AXES:
        if ax in filled or ax not in values or ax in out["skipped_axes"]:
            continue
        g: dict[str, list[str]] = {}
        none = 0
        for c in L:
            vs = list(dict.fromkeys(values[ax].get(c) or []))
            if not vs:
                none += 1
            for v in vs:
                g.setdefault(v, []).append(c)
        if len(g) < 2:
            out["excluded"][ax] = "groups_lt2"
            continue
        if none > len(L) / 2:
            out["excluded"][ax] = "none_over_half"
            continue
        score = max(len(x) for x in g.values())
        out["scores"][ax] = score
        if score >= len(L):  # N14 — 가르지 못함
            out["excluded"][ax] = "all"
            continue
        groups_of[ax] = g
        key = (score, AXES.index(ax))
        if best is None or key < best[0]:
            best = (key, ax)
    if best is None:
        return out
    ax = best[1]
    g = groups_of[ax]

    def first(v: str) -> int:
        return min(order.get(c, len(order)) for c in g[v])

    top = sorted(g, key=lambda v: (-len(g[v]), first(v)))[:OPTIONS_MAX]  # AX5 · N13
    out["axis"] = ax
    out["options"] = [{"value": v, "name": names.get((ax, v), v), "n": len(g[v])} for v in top]
    return out


def level2(kos: KosMap, site: str) -> str:
    """AX3 — 부위를 2 단(얼굴 · 몸 바로 아래)으로 올림 · 1 단(얼굴 · 전신)과 몸 자체는 그대로."""
    if site not in kos.sites:
        return site
    roots = {k for k, v in kos.sites.items() if not v.get("parent_code")}
    face_roots = {k for k in roots if kos.sites[k].get("region") == "face"}
    body_tops = {k for k, v in kos.sites.items() if v.get("parent_code") in roots - face_roots}  # 몸(SITE-147)
    stop = face_roots | body_tops
    if site in roots or site in body_tops:
        return site
    cur = site
    while kos.sites.get(cur, {}).get("parent_code") and kos.sites[cur]["parent_code"] not in stop:
        nxt = kos.sites[cur]["parent_code"]
        if nxt in roots:
            break
        cur = nxt
    return cur


async def _rows(hub, gold: str, tool: str, req: dict, key: str, budget: dict) -> list:
    k = (gold, key, tool + "|" + str(req.get("facet") or ""))
    if k not in _CACHE:
        budget["calls"] += 1
        ra = await hub.read_all(tool, req)
        if not ra.complete:
            raise RuntimeError(ra.error or "incomplete")
        _CACHE[k] = list(ra.items)
    return _CACHE[k]


def _uncached(gold: str, cases: list[str], facets: tuple[str, ...]) -> int:
    return sum(1 for c in cases for f in facets if (gold, f"procedure_case:{c}", "facets|" + f) not in _CACHE)


async def axis_values(
    kos: KosMap, hub, gold: str, L: list[str], *, call_max: int = CALL_MAX
) -> tuple[dict[str, dict[str, list[str]]], dict[tuple[str, str], str], list[str], int]:
    """AX3 · AX6 — (값 표, 이름, 빠진 축, 새 허브 호출 수)."""
    values: dict[str, dict[str, list[str]]] = {ax: {} for ax in ITEM_AXES}
    names: dict[tuple[str, str], str] = {}
    budget = {"calls": 0}
    skipped: list[str] = []
    for c in L:
        sites = [level2(kos, s) for s in kos.item[c]["hub"].get("site_codes") or []]
        values["site"][c] = list(dict.fromkeys(sites))
        for s in sites:
            names[("site", s)] = kos.sites.get(s, {}).get("name") or s
    cases = sorted({x for c in L for x in kos.item[c]["hub"].get("case_codes") or []})
    if _uncached(gold, cases, ("indication", "outcome")) > call_max:  # AX6
        skipped = ["concern", "effect"]
        values.pop("concern")
        values.pop("effect")
        for cs in cases:  # 한도까지는 캐시를 채운다 — 다음 턴에 남은 호출이 한도 안이면 그때 두 축을 센다
            for f in ("indication", "outcome"):
                if budget["calls"] >= call_max:
                    break
                node = f"procedure_case:{cs}"
                await _rows(hub, gold, "facets", {"facet": f, "node": node}, node, budget)
    else:
        con: dict[str, list[str]] = {}
        eff: dict[str, list[str]] = {}
        for cs in cases:
            node = f"procedure_case:{cs}"
            ind = await _rows(hub, gold, "facets", {"facet": "indication", "node": node}, node, budget)
            con[cs] = []
            for r in ind:
                if (
                    (r.get("layer") or {}).get("role") == SELF_ROLE
                    and r.get("reach") == CASE_DIRECT
                    and r.get("concern_code")
                ):
                    con[cs].append(r["concern_code"])
                    names[("concern", r["concern_code"])] = r.get("concern_name") or r["concern_code"]
            out = await _rows(hub, gold, "facets", {"facet": "outcome", "node": node}, node, budget)
            eff[cs] = []
            for r in out:
                if r.get("effect_code"):  # N3 — 빈 경과 줄 뺌
                    eff[cs].append(r["effect_code"])
                    names[("effect", r["effect_code"])] = r.get("effect_name") or r["effect_code"]
        for c in L:
            cc = kos.item[c]["hub"].get("case_codes") or []
            values["concern"][c] = list(dict.fromkeys(x for k in cc for x in con.get(k, [])))
            values["effect"][c] = list(dict.fromkeys(x for k in cc for x in eff.get(k, [])))
    top: dict[str, str] = {}
    for c in L:
        tops = []
        for p in kos.item[c]["hub"].get("products") or []:
            code = p.get("code")
            if not code:
                continue
            if code not in top:
                cur, seen = code, set()
                while cur not in seen:
                    seen.add(cur)
                    holders = await _rows(hub, gold, "scope", {"node": f"product:{cur}"}, f"product:{cur}", budget)
                    up = [h for h in holders if h.get("role") == UPPER_PRODUCT and h.get("holder_table") == "product"]
                    if not up:
                        break
                    names.setdefault(
                        ("product", up[0]["holder_code"]), up[0].get("holder_name") or up[0]["holder_code"]
                    )
                    cur = up[0]["holder_code"]
                top[code] = cur
            names.setdefault(("product", code), p.get("name") or code)
            tops.append(top[code])
        values["product"][c] = list(dict.fromkeys(tops))
    return values, names, skipped, budget["calls"]


def clear_cache() -> None:
    _CACHE.clear()
