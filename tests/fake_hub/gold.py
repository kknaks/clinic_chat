"""모의 골드 도구 코어 9(SPEC-005 §5-2~§5-10 입출력 꼴 · 값은 fixture)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

BUILD = {"id": "fixture-build-1", "build_hash": "fixture0build0h1", "published_at": "2026-10-30T00:00:00Z"}
RULE = "카드 밖 말 금지 — fixture"

NODES: dict[str, dict] = {
    "concern:P2": {"name": "주름", "syn": ["주름살"]},
    "concern:P2-3": {"name": "잔주름", "parent": "concern:P2"},
    "concern:P3": {"name": "처짐", "syn": ["탄력저하"]},
    "site:이마": {"name": "이마"},
    "site:턱선": {"name": "턱선"},
    "purpose:리프팅": {"name": "리프팅"},
    "check_condition:C-PREG": {"name": "임신", "syn": ["임신 준비"]},
    "check_condition:C-PRIOR": {"name": "최근 시술 이력"},
    "technique:K05": {"name": "초음파"},
    "family:M2": {"name": "보톡스", "syn": ["보툴리눔"]},
    "family:M9": {"name": "HIFU 리프팅"},
    "unit_procedure:U5-001": {"name": "이마 보톡스", "family": "family:M2", "site": "site:이마"},
    "unit_procedure:U5-102": {"name": "울쎄라", "family": "family:M9", "syn": ["Ulthera"]},
    "unit_procedure:U5-140": {"name": "소프웨이브", "family": "family:M9", "syn": ["Sofwave"]},
    "product:M9-1-10": {"name": "10THERA", "family": "family:M9"},
    # 시술 2개 의도 fixture(실물 run 꼴 — 보톡스랑 팔자 필러 · CO2 프락셀이랑 필러 · 필러랑 스컬트라)
    "site:팔자": {"name": "팔자"},
    "family:M3": {"name": "필러"},
    "unit_procedure:U5-082": {"name": "팔자 필러", "family": "family:M3", "site": "site:팔자"},
    "unit_procedure:U5-086": {"name": "이마 필러", "family": "family:M3", "site": "site:이마"},
    "family:M7": {"name": "CO2 레이저", "syn": ["CO2"]},
    "unit_procedure:U5-200": {"name": "얼굴 CO2 프락셀", "family": "family:M7"},
    "family:M4": {"name": "콜라겐 부스터"},
    "product:M4-1-1": {"name": "스컬트라", "family": "family:M4"},
    "unit_procedure:U5-300": {"name": "볼 스컬트라", "family": "family:M4"},
}
for _id, _n in NODES.items():
    _n["kind"] = _id.split(":", 1)[0]

INTENTS = [
    ("I-01", "고민 → 추천", "이마 주름이 신경 쓰여요"), ("I-02", "시술 설명", "소프웨이브는 어떻게 하는 거예요?"),
    ("I-03", "주의 · 사후관리", "끝나고 사우나 가도 돼요?"), ("I-04", "부작용 · 합병증", "멍 들어요?"),
    ("I-05", "회복 · 효과 기간", "바로 출근해도 돼요?"), ("I-06", "비교", "울쎄라랑 소프웨이브 뭐가 달라요?"),
    ("I-07", "같이 · 순서 · 간격", "오늘 둘 다 같이 해도 돼요?"), ("I-08", "이전 시술 뒤 가능?", "6주 전에 보톡스 맞았는데 괜찮아요?"),
    ("I-09", "나한테 해도 되나(확인 조건)", "임신 준비 중인데요"), ("I-10", "용량 · 회차 · 제품", "몇 cc 맞아요?"),
    ("I-11", "추천 조합 · 패키지 구성", "같이 하면 좋은 거 있어요?"), ("I-12", "가격", "얼마예요?"),
]
MENUS = ["처짐/탄력"]
TREATS = {"concern:P2": ["unit_procedure:U5-001"], "concern:P2-3": ["unit_procedure:U5-001"],
          "concern:P3": ["unit_procedure:U5-102", "unit_procedure:U5-140"]}
SECTIONS = ["overview", "method", "recovery", "aftercare", "timeline", "emergency", "disclosure", "safety.side_effects",
            "safety.complications", "safety.pih", "checks", "site_caution", "dose", "products", "evidence", "ops",
            "market", "all"]  # SPEC-005 §4-3 이름(10-30 허브 짧은 카드 판)


class GoldError(ValueError):
    pass


def norm(s: str) -> str:
    t = unicodedata.normalize("NFKC", s).replace("쳐짐", "처짐")
    return re.sub(r"[\s()\[\]]", "", t).lower()


def _fact(owner: str, section: str) -> dict:
    return {"id": f"{owner}#{section}#fixture[0]", "text": f"fixture {NODES[owner]['name']} {section}",
            "section": section, "flags": [], "state": "값 있음", "source": "fixture"}


def _safety(uid: str) -> dict:
    return {"id": uid, "side_effects": {"state": "값 있음", "fact_ids": [f"{uid}#safety#fixture[0]"]},
            "pih": {"state": "정보 없음(gap)", "flags": ["gap"]}}


def _wrap(request: dict, body: dict, facts: list[dict], flags: list[str] | None = None) -> dict:
    flags = sorted(set(flags or []))
    return {"build": BUILD, "request": request, **body, "facts": facts, "must_show_flags": flags,
            "claude_contract": {"allowed_fact_ids": [f["id"] for f in facts], "must_show_flags": flags, "rule": RULE}}


def _need(i: str, *kinds: str) -> dict:
    n = NODES.get(i)
    if n is None:
        raise GoldError(f"없는 id: {i}")
    if kinds and n["kind"] not in kinds:
        raise GoldError(f"틀린 종류: {i}(기대 {'|'.join(kinds)})")
    return n


def _card(uid: str) -> dict:
    n = NODES[uid]
    return {"id": uid, "name": n["name"], "family": n.get("family"), "synonyms": n.get("syn", []),
            "safety": _safety(uid), "products": [p for p, x in NODES.items() if x["kind"] == "product"
                                                 and x.get("family") == n.get("family")]}


# T1
def resolve(text: str, kinds: list[str] | None = None, limit_per_span: int = 20, sections: list[str] | None = None, intent: str | None = None) -> dict:
    if not 1 <= len(text) <= 200:
        raise GoldError("text 는 1~200자")
    if limit_per_span > 50:
        raise GoldError("limit_per_span 은 50 이하")
    exprs: dict[str, list[str]] = {}
    for i, n in NODES.items():
        if kinds and n["kind"] not in kinds:
            continue
        for e in [n["name"], *n.get("syn", [])]:
            exprs.setdefault(norm(e), []).append(i)
    menus = {norm(m): m for m in MENUS}
    matches, unmatched = [], []
    for w in [x for x in re.split(r"[\s/·,()\[\]+]+", text) if x]:
        k = norm(w)
        stage, ids = "낱말", exprs.get(k)
        if ids is None:
            for t in ("주사", "시술"):
                if k.endswith(t) and k[: -len(t)] in exprs:
                    stage, ids = "끝말", exprs[k[: -len(t)]]
        if ids is None:
            hit = [i for e, v in exprs.items() for i in v if len(k) >= 2 and e.startswith(k)]
            if hit:
                stage, ids = "접두", sorted(set(hit))
        mc = [{"id": f"menu:{m}", "label": m, "kind": "menu"} for km, m in menus.items() if k and k in km]
        if ids:
            ids = ids[:limit_per_span]
            matches.append({"span": w, "stage": stage, "mode": "하나" if len(ids) == 1 else "후보",
                            "ambiguous": len(ids) > 1, "collision": None, "menu_candidates": mc,
                            "candidates": [{"id": i, "kind": NODES[i]["kind"], "name": NODES[i]["name"],
                                            "qualifier": None, "reasons": ["fixture"],
                                            "flags": ["prefix"] if stage == "접두" else []} for i in ids]})
        elif mc:
            matches.append({"span": w, "stage": "완전", "mode": "하나", "ambiguous": False, "collision": None,
                            "candidates": [], "menu_candidates": mc})
        else:
            unmatched.append(w)
    req = {"text": text, "kinds": kinds, "limit_per_span": limit_per_span}
    return {"build": BUILD, "request": req, "matches": matches, "unmatched": unmatched}


# T2
def _check_sections(sections: list[str] | None) -> None:
    for s in sections or []:
        if s not in SECTIONS:
            raise GoldError(f"없는 section: {s}")


def recommend(concerns: list[str], site: str | None = None, cc: str | None = None,
              include_conditional: bool = False, include_not_recommended: bool = False,
              sections: list[str] | None = None, intent: str | None = None) -> dict:
    if not concerns:
        raise GoldError("concerns 는 1개 이상")
    _check_sections(sections)
    for c in concerns:
        _need(c, "concern")
    if site:
        _need(site, "site")
    units = sorted({u for c in concerns for u in TREATS.get(c, [])})
    cards = [{**_card(u), "why": [{"edge": "treats", "from": c, "source": "fixture", "layer": "부위", "flags": []}
                                  for c in concerns if u in TREATS.get(c, [])]} for u in units]
    facts = [_fact(u, "overview") for u in units]
    req = {"concerns": concerns, "site": site, "cc": cc, "include_conditional": include_conditional,
           "include_not_recommended": include_not_recommended, "sections": sections}
    body = {"areas": [{"site": site or "site:얼굴", "name": NODES.get(site, {}).get("name", "얼굴"),
                       "layer": "부위", "concerns": concerns, "card_ids": units}],
            "cards": cards, "family_only": [], "excluded_counts": {"conditional": 0, "not_recommended": 0}}
    return _wrap(req, body, facts, ["gap"])


# T3
def procedure_card(id: str, product: str | None = None, sections: list[str] | None = None, full: bool = False, intent: str | None = None) -> dict:
    n = _need(id, "unit_procedure", "product", "family")
    _check_sections(sections)
    secs = [s for s in SECTIONS if s != "all"] if full or "all" in (sections or []) else (sections or [])
    facts = [_fact(id, s) for s in secs] or [_fact(id, "overview")]
    card = _card(id) if n["kind"] == "unit_procedure" else {"id": id, "name": n["name"], "kind": n["kind"]}
    if n["kind"] == "family":  # 계열 카드 = 단위 시술 id 목록(SPEC-005 §5-4 · 실물 card.units 꼴)
        card["units"] = [{"id": u, "name": x["name"]} for u, x in NODES.items()
                         if x["kind"] == "unit_procedure" and x.get("family") == id]
    card["sections"] = {s: [f"{id}#{s}#fixture[0]"] for s in secs}
    req = {"id": id, "product": product, "sections": sections, "full": full}
    return _wrap(req, {"card": card}, facts, ["gap"])


# T4
# 허브가 계열 · 제품 id 를 단위 시술로 펼치게 고치는 중(10-30 코디) — 모의는 고친 뒤 꼴로 받음
PROC_IN = ("unit_procedure", "product", "family")


def pair_check(procedures: list[str], prior: dict | None = None, sub_condition: dict | None = None,
               include_conditional: bool = False, sections: list[str] | None = None, intent: str | None = None) -> dict:
    if not 2 <= len(procedures) <= 6:
        raise GoldError("procedures 는 2~6개")
    for p in procedures:
        _need(p, *PROC_IN)
    pairs = []
    for i in range(len(procedures)):
        for j in range(i + 1, len(procedures)):
            a, b = procedures[i], procedures[j]
            pairs.append({"a": a, "b": b, "value": {"first": None, "days": [14, 14] if a != b else [90, 90],
                          "open_high": True, "same_day": a != b, "by_sub_condition": None, "options": [],
                          "field_status": "값 있음", "status": "fixture"},
                          "alternatives": [], "claims": [{"id": "K8-fixture", "kind": "간격", "text": "fixture 간격"}],
                          "edges": ["E8-fixture"]})
    if prior:
        _need(prior["procedure"], "unit_procedure")
        for p in pairs:
            p["prior_ok"] = prior.get("elapsed_days", 0) >= 14
            p["prior_basis"] = "fixture days[0]=14"
    req = {"procedures": procedures, "prior": prior, "sub_condition": sub_condition,
           "include_conditional": include_conditional}
    return _wrap(req, {"pairs": pairs, "global": []}, [_fact(procedures[0], "safety")])


# T5
def check_conditions(id: str, conditions: list[str] | None = None, sections: list[str] | None = None, intent: str | None = None) -> dict:
    _need(id, "unit_procedure", "product", "package")
    conds = conditions or ["check_condition:C-PREG", "check_condition:C-PRIOR"]
    for c in conds:
        _need(c, "check_condition")
    checks = [{"condition": c, "name": NODES[c]["name"], "grade3": "주의", "scope": "family",
               "resolved_id": "R8-fixture", "from": ["fixture"], "alternatives": []} for c in conds]
    return _wrap({"id": id, "conditions": conditions}, {"checks": checks, "waits": [], "not_condition": []},
                 [_fact(id, "checks")])


# T6
def compare(procedures: list[str], sections: list[str] | None = None, intent: str | None = None) -> dict:
    if not 2 <= len(procedures) <= 4:
        raise GoldError("procedures 는 2~4개")
    _check_sections(sections)
    for p in procedures:
        _need(p, *PROC_IN)
    rows = [{"field": "family", "values": {p: NODES[p].get("family") for p in procedures}}]
    body = {"rows": rows, "source_comparisons": [], "alternatives": [], "safety": [_safety(p) for p in procedures]}
    return _wrap({"procedures": procedures, "sections": sections}, body, [_fact(p, "overview") for p in procedures])


# T7
def combos(concern: str | None = None, purpose: str | None = None, procedure: str | None = None,
           menu: str | None = None, include_sales_copy: bool = False, include_not_recommended: bool = False,
           sections: list[str] | None = None, intent: str | None = None) -> dict:
    if not (concern or purpose or procedure or menu):
        raise GoldError("concern · purpose · procedure · menu 중 하나 이상")
    if menu and menu not in MENUS:
        raise GoldError(f"없는 menu: {menu}")
    combo = {"id": "combo:RC-fixture", "kind": "고민 패키지(사이트 「추천 패키지」)", "name": "fixture 조합", "tier": None,
             "anchor": {"source_label": "처짐/탄력"},
             "components": [{"field": "fixture", "family": "family:M9", "units": ["unit_procedure:U5-140"],
                             "status": "값 있음", "flag": None}], "flags": [], "facts": []}
    req = {"concern": concern, "purpose": purpose, "procedure": procedure, "menu": menu,
           "include_sales_copy": include_sales_copy, "include_not_recommended": include_not_recommended}
    return _wrap(req, {"combos": [combo], "reference": [], "packages": []}, [_fact("unit_procedure:U5-140", "overview")])


# T8
def neighbors(id: str, edge_kinds: list[str] | None = None, direction: str = "both", depth: int = 1,
              limit: int = 50, include_conditional: bool = False, include_derived: bool = True,
              sections: list[str] | None = None, intent: str | None = None) -> dict:
    _need(id)
    if depth > 2 or limit > 50:
        raise GoldError("depth ≤ 2 · limit ≤ 50")
    nb = [u for c, us in TREATS.items() if c == id for u in us] or [c for c, us in TREATS.items() if id in us]
    req = {"id": id, "edge_kinds": edge_kinds, "direction": direction, "depth": depth, "limit": limit,
           "include_conditional": include_conditional, "include_derived": include_derived}
    body = {"nodes": [{"id": x, "kind": NODES[x]["kind"], "name": NODES[x]["name"]} for x in nb][:limit],
            "edges": [{"kind": "treats", "a": id, "b": x} for x in nb][:limit], "truncated": False,
            "counts_by_kind": {"treats": len(nb)}}
    return _wrap(req, body, [])


# T9
def vocab(kinds: list[str] | None = None, sections: list[str] | None = None, intent: str | None = None) -> dict:
    all_kinds = ["concern", "site", "purpose", "check_condition", "technique", "family", "intent", "menu"]
    ks = kinds or all_kinds
    out: dict[str, Any] = {}
    for k in ks:
        if k == "intent":
            out[k] = [{"id": i, "name": n, "tool": None} for i, n, _e in INTENTS]  # 실물 허브처럼 예시 없음
        elif k == "menu":
            out[k] = [{"id": f"menu:{m}", "label": m} for m in MENUS]
        elif k in all_kinds:
            out[k] = [{"id": i, "name": n["name"], "synonyms": n.get("syn", []), "parent": n.get("parent")}
                      for i, n in NODES.items() if n["kind"] == k]
        else:
            raise GoldError(f"없는 kind: {k}")
    return {"build": BUILD, "request": {"kinds": kinds}, "vocab": out,
            "enums": {"sections": SECTIONS, "sub_condition": ["세션"]}}


# T10 · T11(SPEC-005 v3.2 §5-16) — Jev 걸음용 peek · expand. 의도 · 동봉 없음 · intent 는 받되 무시(M-3 · 모르는 값만 4xx)
PEEK_ITEMS_PER_KIND = 8
PEEK_NAME_CHARS = 20
EXPAND_SECTION_MAX = 12
EXPAND_NEIGHBORS_MAX = 20
KIND_ORDER = ["procedure_relation", "requires_check", "site_caution", "has_complication", "combo_component",
              "uses_product", "product_unit", "contains", "treats", "concern_avoid", "has_statement"]
VALID_INTENTS = {i for i, _n, _e in INTENTS}
SECTION_FACTS = {"aftercare": 3}  # section 마다 fixture fact 수(기본 1) — more_ids 시험용은 EXPAND_SECTION_MAX 넘게 못 둠


def _intent_ok(intent: str | None) -> None:
    if intent is not None and intent not in VALID_INTENTS:
        raise GoldError(f"없는 intent: {intent}")


def _edges(i: str) -> dict[str, list[tuple[str, str]]]:
    """노드 하나의 이웃 — {edge kind: [(이웃 id, 라벨)]} (fixture 그래프: 계열 · 고민 · 부위 · 제품 · 확인 조건 · 쌍)."""
    n = NODES[i]
    out: dict[str, list[tuple[str, str]]] = {}
    add = lambda k, x, lab="": out.setdefault(k, []).append((x, lab))
    if n["kind"] == "unit_procedure":
        if n.get("family"):
            add("contains", n["family"])
        for c, us in TREATS.items():
            if i in us:
                add("treats", c)
        if n.get("site"):
            add("site_caution", n["site"], "부위 주의")
        for p, x in NODES.items():
            if x["kind"] == "product" and x.get("family") == n.get("family"):
                add("uses_product", p)
        for c in ("check_condition:C-PREG", "check_condition:C-PRIOR"):
            add("requires_check", c, "주의")
        for u, x in NODES.items():
            if x["kind"] == "unit_procedure" and u != i and x.get("family") == n.get("family"):
                add("procedure_relation", u, "first=None · days=14 · same_day=True")
    elif n["kind"] == "family":
        for u, x in NODES.items():
            if x["kind"] == "unit_procedure" and x.get("family") == i:
                add("contains", u)
    elif n["kind"] == "concern":
        for u in TREATS.get(i, []):
            add("treats", u)
    elif n["kind"] == "product" and n.get("family"):
        add("contains", n["family"])
    return out


def _kind_rank(k: str) -> tuple[int, str]:
    return (KIND_ORDER.index(k), k) if k in KIND_ORDER else (len(KIND_ORDER), k)


def _sections_of(i: str) -> list[dict]:
    if NODES[i]["kind"] not in ("unit_procedure", "family", "product"):
        return []
    out = []
    for sec in (x for x in SECTIONS if x != "all"):
        fs = _sec_facts(i, sec)
        out.append({"name": sec, "n_facts": len(fs), "bytes": sum(len(f["text"].encode()) for f in fs)})
    return out


def _sec_facts(i: str, sec: str) -> list[dict]:
    return [{**_fact(i, sec), "id": f"{i}#{sec}#fixture[{k}]", "text": f"fixture {NODES[i]['name']} {sec} {k}"}
            for k in range(SECTION_FACTS.get(sec, 1))]


def peek(id: str, include_conditional: bool = False, intent: str | None = None) -> dict:
    n = _need(id)
    _intent_ok(intent)
    by_kind = {}
    for k, xs in sorted(_edges(id).items(), key=lambda kv: _kind_rank(kv[0])):
        by_kind[k] = {"n": len(xs), "direction": "out",
                      "items": [{"id": x, "kind": NODES[x]["kind"], "name": NODES[x]["name"][:PEEK_NAME_CHARS],
                                 "label": lab or k} for x, lab in xs[:PEEK_ITEMS_PER_KIND]]}
    node = {"id": id, "kind": n["kind"], "name": n["name"], "flags": [], "gate_status": "통과", "default_out": False}
    req = {"id": id, "include_conditional": include_conditional, "intent": intent}
    return {"build": BUILD, "request": req, "intent_used": None, "node": node,
            "safety": _safety(id) if n["kind"] == "unit_procedure" else {},
            "neighbors_by_kind": by_kind, "sections": [x for x in _sections_of(id) if x["n_facts"]],
            "priority_hint": [k for k in ("site_caution", "requires_check") if k in by_kind],
            "claude_contract": {"allowed_fact_ids": [], "rule": RULE}}


def expand(id: str, sections: list[str] | None = None, neighbors: list[str] | None = None,
           neighbor_kinds: list[str] | None = None, include_conditional: bool = False,
           fact_ids: list[str] | None = None, intent: str | None = None) -> dict:
    n = _need(id)
    _intent_ok(intent)
    _check_sections(sections)
    edges = _edges(id)
    peeked = {x: k for k, xs in edges.items() for x, _ in xs[:PEEK_ITEMS_PER_KIND]}
    for x in neighbors or []:
        if x not in peeked:
            raise GoldError(f"peek 가 낸 이웃 아님: {x}")
    pick = [(x, peeked[x]) for x in neighbors or []]
    for k in neighbor_kinds or []:
        pick += [(x, k) for x, _ in edges.get(k, []) if (x, k) not in pick]
    secs: dict[str, dict] = {}
    facts: list[dict] = []
    for sec in sections or []:
        fs = _sec_facts(id, sec)
        secs[sec] = {"facts": fs[:EXPAND_SECTION_MAX], "more_ids": [f["id"] for f in fs[EXPAND_SECTION_MAX:]],
                     "total": len(fs)}
        facts += fs[:EXPAND_SECTION_MAX]
    nbs = [{"id": x, "kind": NODES[x]["kind"], "name": NODES[x]["name"], "edge": k,
            **({"safety": _safety(x)} if NODES[x]["kind"] == "unit_procedure" else {}),
            "sections_n": {s["name"]: s["n_facts"] for s in _sections_of(x) if s["n_facts"]}}
           for x, k in pick[:EXPAND_NEIGHBORS_MAX]]
    body = {"node": {"id": id, "kind": n["kind"], "name": n["name"]}, "sections": secs, "neighbors": nbs,
            "truncated": len(pick) > EXPAND_NEIGHBORS_MAX, "more_ids": [x for x, _ in pick[EXPAND_NEIGHBORS_MAX:]],
            "intent_used": None}
    req = {"id": id, "sections": sections, "neighbors": neighbors, "neighbor_kinds": neighbor_kinds,
           "include_conditional": include_conditional, "fact_ids": fact_ids, "intent": intent}
    out = _wrap(req, body, facts)
    out["claude_contract"]["priority"] = ["safety", "site_caution", "checks"]
    return out


def _with_sections_check(fn):
    def wrapped(*a, **kw):
        _check_sections(kw.get("sections"))
        return fn(*a, **kw)

    wrapped.__name__ = fn.__name__
    wrapped.__signature__ = __import__("inspect").signature(fn)
    wrapped.__annotations__ = fn.__annotations__
    return wrapped


# 허브 짧은 카드 판(10-30 · 4faa88b): 도구 9 모두 sections 입력 — 모르는 이름은 4xx
TOOLS = {"resolve": resolve, "recommend": recommend, "procedure_card": procedure_card, "pair_check": pair_check,
         "check_conditions": check_conditions, "compare": compare, "combos": combos, "neighbors": neighbors,
         "vocab": vocab, "peek": peek, "expand": expand}
TOOLS = {k: _with_sections_check(v) for k, v in TOOLS.items()}
