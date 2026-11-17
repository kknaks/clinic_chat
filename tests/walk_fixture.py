"""걷기 시험용 모의 허브 시나리오 — 가짜 코드 · 가짜 이름. 응답은 손으로 적은 꼴뿐(규칙 계산 없음).

꼴은 WORK-005 §3(`matched{span, start, covered_by}`) · §4(`coverage`)와 같다. `facets` 는 손으로 적은 행 가운데
`roles` 로 준 role 의 행만 돌려준다(허브의 role 거름을 흉내 — 「하위 *」 role 을 주면 `layer.role` = 그 role 행이 나온다).
"""

from __future__ import annotations

from tests.fake_hub import FakeHub, row

P1 = {"entity_table": "procedure", "entity_code": "ZZP-1", "entity_name": "가짜시술", "text": "가짜시술"}
C1 = {"entity_table": "concern", "entity_code": "ZZC-1", "entity_name": "가짜고민", "text": "가짜고민"}
A1 = {"entity_table": "adverse", "entity_code": "ZZA-1", "entity_name": "가짜사건", "text": "가짜사건"}
PR = {"entity_table": "product", "entity_code": "ZZR-1", "entity_name": "가짜제품", "text": "가짜제품"}
K_ROWS = ("self", "lower", "inherited")


def cand(c: dict, span: str | None = None, start: int = 0, covered_by: str | None = None) -> dict:
    return {"gold_table": "g_entry", "gid": f"GEN-9{c['entity_code'][-1]}000", **c,
            "matched": {"span": span or c["text"], "start": start, "covered_by": covered_by}}


def lay(role: str = "자기", holder: str = "ZZP-1", name: str = "가짜시술", table: str = "procedure") -> dict:
    return {"role": role, "path": "", "holder": {"holder_table": table, "holder_code": holder, "holder_name": name}}


def profile_rows(n: int = 3) -> list[dict]:
    return [row("g_profile", f"GPF-9100{i}", subject_table="procedure", subject_code="ZZP-1", name_ko="가짜시술",
                overview="가짜 개요", mechanism_name="" if i else "가짜원리",
                layer=lay("자기" if i == 0 else "물려받음", "ZZD-1" if i else "ZZP-1", "가짜계열" if i else "가짜시술",
                          "modality" if i else "procedure")) for i in range(n)]


def scope_rows() -> list[dict]:
    return [row("g_scope", f"GSC-9100{i}", subject_table="procedure", subject_code="ZZP-1", holder_table="procedure_case",
                holder_code=f"ZZK-{i}", holder_name=f"가짜케이스{i}", role="하위 케이스", subject_name="가짜시술")
            for i in range(3)]


def product_rows() -> dict[str, list[dict]]:
    """자기 행 0 인 상위 제품(e2e 3 의 꼴) — 효과는 하위 제품에만 · 원리는 물려받은 층에만 · 개요는 자기 행."""
    sub = lay("하위 제품", "ZZR-2", "가짜하위제품", "product")
    inh = lay("물려받음", "ZZP-1", "가짜시술")
    return {
        "profile": [row("g_profile", "GPF-97000", subject_table="product", subject_code="ZZR-1", name_ko="가짜제품",
                        overview="제품 개요", mechanism_name="", layer=lay("자기", "ZZR-1", "가짜제품", "product")),
                    row("g_profile", "GPF-97001", subject_table="procedure", subject_code="ZZP-1", name_ko="가짜시술",
                        mechanism_name="가짜원리", layer=inh)],
        "outcome": [row("g_outcome", f"GOU-9700{i}", target_table="product", target_code="ZZR-2",
                        target_name="가짜하위제품", effect_name=f"가짜효과{i}", layer=sub) for i in range(4)]
                   + [row("g_outcome", "GOU-97100", target_table="procedure", target_code="ZZP-1", target_name="가짜시술",
                          effect_name="윗층효과", layer=inh)],
    }


def make_hub(*, resolve: list[dict] | None = None, list_mode: list[dict] | None = None, fail: set[str] | None = None,
             cc_groups: int = 0, cc_rows0_lck: bool = False, pkg_upper: int = 0, coverage: dict | None = None,
             facet_rows: dict | None = None, pair_rows: list[dict] | None = None) -> FakeHub:
    """`coverage` = {node: {kind: (self, lower, inherited)}} · `facet_rows` = {facet: [행]}(노드 무관 — 대상 쪽)."""
    def resolve_fn(p):
        if p.get("text"):
            return {"candidates": resolve or [], "ambiguous": False, "total": len(resolve or []), "next_cursor": None}
        return {"candidates": list_mode or []}

    def by_roles(rows, p):
        roles = p.get("roles")
        if not roles:
            return rows
        want = set(roles.split(","))
        return [r for r in rows if (r.get("layer") or {}).get("role", "자기") in want]

    def facets_fn(p):
        f = p.get("facet")
        node = p.get("node", "")
        if facet_rows is not None and f in facet_rows and not node.startswith("procedure_case:"):
            return {"rows": by_roles(facet_rows[f], p), "ledger_check": []}
        if f == "profile":
            if node.startswith("procedure_case:"):
                return {"rows": [row("g_profile", f"GPF-93{node[-1]}00", subject_table="procedure_case",
                                     subject_code=node.split(':')[1], name_ko="가짜케이스", procedure_name="가짜시술",
                                     layer=lay())], "ledger_check": []}
            return {"rows": by_roles(profile_rows(), p), "ledger_check": []}
        if f == "indication":
            return {"rows": [row("g_indication", "GIN-91001", candidate_table="procedure", candidate_code="ZZP-1",
                                 reach="시술 직접", layer=lay())], "ledger_check": []}
        if f == "outcome":
            return {"rows": [row("g_outcome", "GOU-91001", target_table="procedure", target_code="ZZP-1",
                                 effect_code="ZZE-1", layer=lay())], "ledger_check": []}
        if f == "adverse":
            return {"rows": [row("g_adverse", "GAD-91001", target_table="procedure", target_code="ZZP-1",
                                 adverse_name="가짜사건", polarity="보고", layer=lay())], "ledger_check": []}
        if f == "package":
            up = [row("g_package", f"GPK-9500{i}", package_id=f"ZZG-{i}", concern_code="ZZC-0",
                      hier={"asked_code": "ZZC-1", "match": "상위"}) for i in range(pkg_upper)]
            return {"rows": up, "ledger_check": [{"code": "ZZC-1", "lck_ids": ["LCK-900"], "on": "request"}]
                    if cc_rows0_lck else []}
        return {"rows": [], "ledger_check": []}

    def cc_fn(p):
        groups = [{"candidate_table": "procedure_case", "candidate_code": f"ZZK-{i}", "candidate_name": f"가짜케이스{i}",
                   "procedure_code": "ZZP-1", "procedure_name": "가짜시술", "reach": ["케이스 직접"], "n_rows": 1,
                   "rows": [row("g_indication", f"GIN-920{i:02d}", candidate_table="procedure_case",
                                candidate_code=f"ZZK-{i}", procedure_code="ZZP-1", reach="케이스 직접")]}
                  for i in range(cc_groups)]
        lc = [{"code": "ZZC-1", "lck_ids": ["LCK-900"], "on": "request"}] if cc_rows0_lck else []
        return {"groups": groups, "modality_only": [], "unreached": [], "ledger_check": lc}

    def coverage_fn(p):
        nodes = [x for x in (p.get("nodes") or "").split(",") if x]
        if len(nodes) > 64:
            return 422, {"detail": "invalid_argument: nodes > 64"}
        items = []
        for n in nodes:
            ks = (coverage or {}).get(n, {})
            items.append({"node": n, "name": "가짜", "kinds": [
                {"kind": k, **dict(zip(K_ROWS, v, strict=True)), "lower_holders": 1 if v[1] else 0}
                for k, v in ks.items()]})
        return {"request": {"nodes": nodes, "kinds": None}, "total": len(items), "next_cursor": None, "items": items}

    return FakeHub({"resolve": resolve_fn, "facets": facets_fn, "scope": {"holders": scope_rows()},
                    "concern_candidates": cc_fn,
                    "pair": {"rows": pair_rows or [], "same_day_packages": [], "ledger_check": []},
                    "coverage": coverage_fn,
                    "health": {"build": {"gold_hash": "fakehash", "aux_hash": "fakeaux"}}},
                   fail_on=fail or set())


def jev_noul(*, target=0.9, kinds: dict | None = None, gap=0.1, other=0.9, entry=0.1):
    """모의 Jev noul — `kinds` = {code: noul}(없는 종류는 0.1) · 이름으로만 가른다(instructions 를 해석하지 않는다)."""
    from clinic_chat.walk.kinds import KIND_CODES

    kv = kinds if kinds is not None else {"overview": 0.9}

    def f(name, instr, state):
        if name.startswith("target_"):
            return target(int(name.split("_")[1]), state) if callable(target) else target
        if name.startswith("kind_"):
            return kv.get(KIND_CODES[int(name.split("_")[1])], 0.1)
        if name == "gap":
            return gap
        if name.startswith("entry_"):
            return entry(name) if callable(entry) else entry
        return other(name) if callable(other) else other
    return f
