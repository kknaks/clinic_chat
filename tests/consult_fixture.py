"""상담 시험용 모의 허브 시나리오(가짜 코드 · 가짜 이름 · 손으로 적은 꼴 — 규칙 계산 없음).

고민 `ZZC-1` 의 `concern_candidates`: 시술 ZZP-1(계열 가짜계열A — 시술 · 제품 두 후보) · 시술 ZZP-2(가짜계열B) ·
「계열 층만」 하나 · 「그 밖」 하나. 후보마다 profile(계열 이름 · 원리 이름). 부위 머리 칸은 `sites` 를 줄 때만.
"""

from __future__ import annotations

from tests.fake_hub import FakeHub, row
from tests.walk_fixture import C1, P1, cand

P2 = {"entity_table": "procedure", "entity_code": "ZZP-2", "entity_name": "가짜시술둘", "text": "가짜시술둘"}


def ind(gid: str, ctable: str, ccode: str, pcode: str, reach: str, match: str = "직접", **kw) -> dict:
    return row("g_indication", gid, candidate_table=ctable, candidate_code=ccode, procedure_code=pcode,
               reach=reach, concern_match=match, concern_code="ZZC-1", asked_concern_code="ZZC-1",
               asked_concern_name="가짜고민", **kw)


GROUPS = [
    {"candidate_table": "procedure", "candidate_code": "ZZP-1", "candidate_name": "가짜시술", "procedure_code": "ZZP-1",
     "procedure_name": "가짜시술", "reach": ["시술 직접"], "concern_match": ["직접"], "n_rows": 1,
     "rows": [ind("GIN-95001", "procedure", "ZZP-1", "ZZP-1", "시술 직접")]},
    {"candidate_table": "procedure", "candidate_code": "ZZP-2", "candidate_name": "가짜시술둘", "procedure_code": "ZZP-2",
     "procedure_name": "가짜시술둘", "reach": ["시술 직접"], "concern_match": ["하위 고민"], "n_rows": 1,
     "rows": [ind("GIN-95002", "procedure", "ZZP-2", "ZZP-2", "시술 직접", "하위 고민")]},
    {"candidate_table": "product", "candidate_code": "ZZR-1", "candidate_name": "가짜제품", "procedure_code": "ZZP-1",
     "procedure_name": "가짜시술", "reach": ["제품 → 시술"], "concern_match": ["직접"], "n_rows": 1,
     "rows": [ind("GIN-95003", "product", "ZZR-1", "ZZP-1", "제품 → 시술")]},
]
MOD_ONLY = [ind("GIN-95004", "modality", "ZZM-9", "", "계열 층만", candidate_name="가짜계열C")]
UNREACHED = [ind("GIN-95005", "product", "ZZR-9", "", "제품 — 케이스 없음", candidate_name="가짜제품없음")]
PROFILES = {
    "procedure:ZZP-1": row("g_profile", "GPF-95001", subject_table="procedure", subject_code="ZZP-1", name_ko="가짜시술",
                           procedure_name="가짜시술", modality_name="가짜계열A", mechanism_name="가짜원리A", overview="개요1"),
    "procedure:ZZP-2": row("g_profile", "GPF-95002", subject_table="procedure", subject_code="ZZP-2", name_ko="가짜시술둘",
                           procedure_name="가짜시술둘", modality_name="가짜계열B", mechanism_name="가짜원리B", overview="개요2"),
    "product:ZZR-1": row("g_profile", "GPF-95003", subject_table="product", subject_code="ZZR-1", name_ko="가짜제품",
                         procedure_name="가짜시술", modality_name="가짜계열A", mechanism_name="가짜원리A", overview="개요3"),
}


def consult_hub(*, sites: list[dict] | None = None, sites_field: str = "concern_sites", narrowed: bool = False,
                gold_hash: str = "fakehash") -> FakeHub:
    def resolve_fn(p):
        t = p.get("text") or ""
        out = []
        if "가짜고민" in t:
            out.append(cand(C1, start=t.index("가짜고민")))
        if "가짜시술둘" in t:
            out.append(cand(P2, start=t.index("가짜시술둘")))
        elif "가짜시술" in t:
            out.append(cand(P1, start=t.index("가짜시술")))
        return {"candidates": out, "ambiguous": False, "total": len(out), "next_cursor": None}

    def cc_fn(p):
        body = {"groups": GROUPS, "modality_only": MOD_ONLY, "unreached": UNREACHED, "ledger_check": [],
                "inherited_from": None, "counts": {"rows": 5}}
        if sites is not None:
            body[sites_field] = sites
        return body

    def facets_fn(p):
        node = p.get("node", "")
        if p.get("facet") == "profile" and node in PROFILES:
            return {"rows": [PROFILES[node]], "ledger_check": []}
        return {"rows": [], "ledger_check": []}

    def coverage_fn(p):
        nodes = [x for x in (p.get("nodes") or "").split(",") if x]
        return {"request": {"nodes": nodes}, "total": len(nodes), "next_cursor": None,
                "items": [{"node": n, "name": "가짜", "kinds": [{"kind": "overview", "self": 1, "lower": 0, "inherited": 0,
                                                                 "lower_holders": 0}]} for n in nodes]}

    b = {"gold_hash": gold_hash, "aux_hash": "fakeaux"}
    hub = FakeHub({"resolve": resolve_fn, "concern_candidates": cc_fn, "facets": facets_fn, "coverage": coverage_fn,
                   "pair": {"rows": [], "same_day_packages": [], "ledger_check": []}, "health": {"build": b}},
                  limit_cap=50)
    orig = hub.handle

    def handle(request):
        resp = orig(request)
        import json as _j

        import httpx2

        body = _j.loads(resp.content)
        if isinstance(body, dict) and "build" in body:
            body["build"] = {**body["build"], "gold_hash": gold_hash}
        return httpx2.Response(resp.status_code, json=body)

    hub.handle = handle  # type: ignore[method-assign]
    return hub
