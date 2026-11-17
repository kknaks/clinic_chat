"""0.6 WP-3 — 거르개(BR6 · T30) · 사건을 이름 댄 조회의 상세(BR5 · T34) · 모의 허브(가짜 이름)."""

from __future__ import annotations

import json

from clinic_chat.columns import col_b
from clinic_chat.evalset import answer_key as ak
from clinic_chat.walk.calls import Entry, plan_calls
from tests.fake_hub import FakeHub, row
from tests.test_walk_core import deps
from tests.walk_fixture import A1, P1, cand, jev_noul, lay, make_hub

S1 = {"entity_table": "site", "entity_code": "ZZS-1", "entity_name": "가짜", "text": "가짜"}
ROLES = ["자기", "물려받음"]


def facets(calls, facet):
    return [c for c in calls if c.request.get("facet") == facet]


def test_t30_br6_named_and_uncertain_site_read_both_yes_only_filtered():
    """T30 — `named` · `uncertain_kept` 부위 입구 → 거른 조회 + 안 거른 조회(거른 쪽에 표지) · yes 부위 → 거른 조회만."""
    t = Entry("procedure", "ZZP-1", "가짜시술", "target")
    for by, both in (("named", True), ("uncertain_kept", True), ("target", False)):
        site = Entry("site", "ZZS-1", "가짜", by)
        cs = facets(plan_calls([t, site], ["outcome"]), "outcome")
        flt = [c for c in cs if c.request.get("filters")]
        plain = [c for c in cs if not c.request.get("filters")]
        assert len(flt) == 1 and len(plain) == (1 if both else 0), by
        assert (flt[0].meta.get("mark") == "narrowed") is both, by
    # 덮인 조각은 그대로 거르개로 쓰지 않는다
    owner = Entry("procedure", "ZZP-1", "가짜시술", "target", span="가짜시술")
    covered = Entry("site", "ZZS-1", "가짜", "named", covered_by="가짜시술")
    assert not [c for c in plan_calls([owner, covered], ["outcome"]) if c.request.get("filters")]


def _adverse_hub(detail_log):
    adv = [row("g_adverse", f"GAD-9800{i}", target_table="procedure", target_code="ZZP-1", adverse_code="ZZA-1",
               adverse_name="가짜사건", polarity="보고", silver_table="adverse_risk", silver_id=sid, layer=lay())
           for i, sid in enumerate(("ZR-2", "ZR-1", "ZR-1"))]

    def facets_fn(p):
        f = p.get("facet")
        flt = p.get("filters") or {}
        if f == "adverse":
            return {"rows": adv if flt.get("adverse_code") == "ZZA-1" or not flt else [], "ledger_check": []}
        if f == "adverse_detail":
            detail_log.append(dict(flt))
            rid = flt.get("adverse_risk_id")
            rs = [row("g_adverse_detail", f"GAX-98{n}", target_table="procedure", target_code="ZZP-1",
                      adverse_risk_id=f"ZR-{n % 3}", detail_kind="징후", layer=lay()) for n in range(100, 106)]
            return {"rows": [r for r in rs if rid is None or r["adverse_risk_id"] == rid], "ledger_check": []}
        return {"rows": [], "ledger_check": []}
    return facets_fn


async def test_t34_br5_detail_by_risk_id_and_same_calls_as_key(tmp_path):
    """T34 — 사건 필터 조회 → `adverse_detail` 이 그 사건 위험 id 로만(오름차순 · id 마다) · 사건 없는 조회는 필터 없음 ·
    B 가 읽은 조회 집합 = 키 명세가 읽는 조회 집합(같은 `plan_calls` · `followup_calls`)."""
    entries = [Entry("procedure", "ZZP-1", "가짜시술", "target"), Entry("adverse", "ZZA-1", "가짜사건", "target")]
    planned = plan_calls(entries, ["adverse"])
    assert [c.request.get("facet") for c in planned] == ["adverse"] and planned[0].request["filters"] == {
        "adverse_code": "ZZA-1"}
    assert [c.request.get("facet") for c in plan_calls(entries[:1], ["adverse"])] == ["adverse", "adverse_detail"]
    # B
    log_b: list = []
    hub = make_hub(resolve=[cand(P1, start=0), cand(A1, start=5)])
    hub.routes["facets"] = _adverse_hub(log_b)
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"adverse": 0.9})))
    assert log_b == [{"adverse_risk_id": "ZR-1"}, {"adverse_risk_id": "ZR-2"}]
    got_b = {json.dumps([r.call.tool, r.call.request], sort_keys=True) for r in w.reads if r.call.purpose == "read"}
    details = [g for g in w.verified.gids() if g.startswith("GAX")]
    assert sorted(details) == ["GAX-98100", "GAX-98101", "GAX-98103", "GAX-98104"]  # id 0 의 행은 안 읽힘
    # 키 — 같은 명세(계획 조회) → 읽은 조회(이어 읽기 포함) 기록이 같다
    log_k: list = []
    kh = FakeHub({"facets": _adverse_hub(log_k)})
    spec = [{"tool": c.tool, "request": c.request, "kinds": list(c.kinds)} for c in planned]
    rows, recs, *_ = await ak.read_rows(kh.client(), spec)
    got_k = {json.dumps([r["tool"], r["request"]], sort_keys=True) for r in recs}
    assert got_b == got_k and log_k == log_b
    assert {r["gid"] for r in rows if r["gold_table"] == "g_adverse_detail"} == set(details)
