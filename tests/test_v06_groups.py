"""0.6 WP-2 — 무리 · 짝 조회의 끝(PR1 ~ PR5 · BR7 · W-4 · B-3 · T28 · T29) · 모의 허브 · 모의 Jev(가짜 이름)."""

from __future__ import annotations

from clinic_chat.columns import col_b
from clinic_chat.walk import end_state as es
from clinic_chat.walk.calls import Entry, plan_calls
from clinic_chat.walk.groups import group_targets
from tests.fake_hub import row
from tests.test_walk_core import deps, req
from tests.walk_fixture import P1, PR, cand, jev_noul, lay, make_hub

MD = {"entity_table": "modality", "entity_code": "ZZD-1", "entity_name": "가짜계열", "text": "가짜계열"}
P2 = {"entity_table": "procedure", "entity_code": "ZZP-2", "entity_name": "다른시술", "text": "다른시술"}


def pairs(calls):
    return [(c.request.get("a"), c.request.get("b"), c.meta.get("mark") or c.meta.get("widened"))
            for c in calls if c.tool == "pair"]


def E(code: str, table: str = "procedure", by: str = "target", **kw) -> Entry:
    return Entry(table, code, "가짜", by, **kw)


def test_t28_a_same_piece_three_concepts_one_end_each():
    """(a) 같은 조각의 개념 셋 → 두 끝 0 · 한 끝 셋."""
    ts = [E("ZZP-1", span="가짜", start=0), E("ZZR-1", "product", span="가짜", start=0),
          E("ZZD-1", "modality", span="가짜", start=0)]
    gs, bys = group_targets(ts)
    assert len(gs) == 1 and bys == ["span"]
    got = pairs(plan_calls(ts, ["combo"], groups=[[t.node for t in g] for g in gs]))
    assert got == [(t.node, None, None) for t in ts]
    assert pairs(plan_calls(ts, ["combo"])) == got  # 무리를 안 줘도 같은 조각이면 한 무리(span)


def test_t28_b_one_lineage_two_pieces_via_scope():
    """(b) 한 계보의 두 조각 — scope 의 물려받음 / 하위 * 로 이어지면 한 무리 → 한 끝."""
    ts = [E("ZZR-1", "product", span="가짜제품", start=0), E("ZZD-1", "modality", span="가짜계열", start=5)]
    links = {"product:ZZR-1": {"modality:ZZD-1": "물려받음"}, "modality:ZZD-1": {}}
    gs, bys = group_targets(ts, links)
    assert len(gs) == 1 and bys == ["scope"]
    assert pairs(plan_calls(ts, ["combo"], groups=[[t.node for t in g] for g in gs])) == \
        [("product:ZZR-1", None, None), ("modality:ZZD-1", None, None)]


def test_t28_c_two_yes_groups_two_end_only():
    """(c) 서로 다른 두 무리(둘 다 yes) → 무리 사이 두 끝만 · 0 행이어도 한 끝 없음(계획에 한 끝이 없다)."""
    ts = [E("ZZP-1", span="가", start=0), E("ZZR-1", "product", span="가", start=0), E("ZZP-2", span="다", start=4)]
    got = pairs(plan_calls(ts, ["combo"], groups=[["procedure:ZZP-1", "product:ZZR-1"], ["procedure:ZZP-2"]]))
    assert got == [("procedure:ZZP-1", "procedure:ZZP-2", None), ("product:ZZR-1", "procedure:ZZP-2", None)]


def test_t28_d_w4_weak_group_adds_yes_group_one_end_and_mark():
    """(d) W-4 — yes 무리 + yes 아닌 무리(named) → 두 끝 + yes 무리의 한 끝 · 두 끝(좁힌 쪽) 행에 표지."""
    ts = [E("ZZP-1"), E("ZZP-2", by="named")]
    got = pairs(plan_calls(ts, ["combo"], groups=[["procedure:ZZP-1"], ["procedure:ZZP-2"]]))
    assert got == [("procedure:ZZP-1", "procedure:ZZP-2", "narrowed"), ("procedure:ZZP-1", None, "w4")]


def test_t28_e_b3_no_yes_group_one_end_per_group():
    """(e) B-3(코디) — yes 무리 0(전부 yes 아님) → 두 끝 + 무리마다 한 끝."""
    ts = [E("ZZP-1", by="uncertain_kept"), E("ZZP-2", by="named")]
    got = pairs(plan_calls(ts, ["combo"], groups=[["procedure:ZZP-1"], ["procedure:ZZP-2"]]))
    assert got == [("procedure:ZZP-1", "procedure:ZZP-2", "narrowed"), ("procedure:ZZP-1", None, "b3"),
                   ("procedure:ZZP-2", None, "b3")]


def _pair_hub(**kw):
    hub = make_hub(**kw)
    one = [row("g_pair", f"GPR-9300{i}", from_table="procedure", from_code="ZZP-1", to_table="procedure",
               to_code=f"ZZX-{i}", to_name=f"짝{i}", a_side="from", question_class="보완", same_day="",
               a_layer=lay()) for i in range(3)]
    two = one[:1]  # N-1 — 두 끝이 일부만 내는 꼴

    def pair_fn(p):
        return {"rows": two if p.get("b") else one, "same_day_packages": [], "ledger_check": []}
    hub.routes["pair"] = pair_fn
    return hub


async def test_t29_one_end_rows_meet_required_and_ok(tmp_path):
    """T29 · PR5 — (a) 꼴에서 한 끝 조회에 행이 있으면 required 충족 · 「근거 충족」 · front.groups 기록."""
    span = {"span": "가짜", "start": 0}
    hub = _pair_hub(resolve=[cand(P1, **span), cand(PR, **span), cand(MD, **span)])
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"combo": 0.9})))
    f = w.front
    assert f.trace_front["groups"] == [{"members": ["procedure:ZZP-1", "product:ZZR-1", "modality:ZZD-1"],
                                        "by": "span"}]
    reads = [r.call for r in w.reads if r.call.tool == "pair" and r.call.purpose == "read"]
    assert reads and all(not c.request.get("b") for c in reads)
    r = req(w, "combo")
    assert r["met_by"] == "self" and w.end_state == es.OK, (w.depth.required, w.end_state)
    assert any(v["purpose"] == "group" for v in w.ledger.visited)


async def test_w4_review_group_scope_does_not_hide_lineup(tmp_path):
    """검수 W-4 — 무리 판정 scope 는 장부 `calls_done` 에 안 든다 → 개요 yes(KD9 → lineup)의 scope 읽기가 그대로 읽힌다."""
    hub = make_hub(resolve=[cand(P1, span="가짜", start=0), cand(P2, span="다른시술", start=3)])
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"overview": 0.9})))
    lineup = [r for r in w.reads if r.call.tool == "scope" and "lineup" in r.call.kinds]
    assert len(lineup) == 2 and all(r.gids for r in lineup)
    assert len({v["node"] for v in w.ledger.visited if v["purpose"] == "group"}) == 2


async def test_key_spec_uses_same_group_function():
    """답 키 명세 — 세트 입구(span 없음)는 scope 로만 묶인다 · 무리 판정 scope 는 명세 calls[] 에 없다."""
    from clinic_chat.evalset.key_calls import build_spec
    from tests.fake_hub import FakeHub

    def scope_fn(p):
        if p.get("node") == "product:ZZR-1":
            return {"holders": [{"gold_table": "g_scope", "gid": "GSC-99001", "role": "물려받음",
                                 "holder_table": "procedure", "holder_code": "ZZP-1"}]}
        return {"holders": []}
    hub = FakeHub({"scope": scope_fn})
    q = {"id": "Q99", "range": "시술 조합"}
    item = {"expected_entries": ["procedure:ZZP-1", "product:ZZR-1"], "expected_kinds": ["combo"]}
    spec = await build_spec(q, hub.client(), {}, item, {})
    assert [(c["tool"], c["request"].get("a"), c["request"].get("b")) for c in spec["calls"]] == \
        [("pair", "procedure:ZZP-1", None), ("pair", "product:ZZR-1", None)]
    hub2 = FakeHub({})
    spec2 = await build_spec(q, hub2.client(), {}, item, {})
    assert [(c["request"].get("a"), c["request"].get("b")) for c in spec2["calls"]] == [("procedure:ZZP-1", "product:ZZR-1")]
