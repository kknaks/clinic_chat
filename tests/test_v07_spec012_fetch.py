"""SPEC-012 0.1.2 WP-2 — 가져오기 · 안전(HF1 부위 거르개 끔 · HF2 holder 부위 가족 · HK1 안전 늘 · HN1 층 없는 줄 주인).

외부 호출 0.
"""

from __future__ import annotations

from types import SimpleNamespace

from clinic_chat.consult import fetch_read as fr
from clinic_chat.consult import kinds_pick as kp
from clinic_chat.consult.material_read import safety_always
from clinic_chat.walk.calls import SITE_NAME_OFF, Entry, plan_calls


def _facets(calls, facet):
    return [c for c in calls if c.request.get("facet") == facet]


def test_hf1_site_name_filter_off_only_in_consult_walk():
    t = Entry("procedure", "ZZP-1", "가짜시술", "target")
    s = Entry("site", "ZZS-1", "가짜", "target")
    on = _facets(plan_calls([t, s], ["adverse", "outcome"]), "adverse")
    assert on and all(c.request.get("filters") == {"site_name": "가짜"} for c in on)  # 시험 앱 걷기(기본) 그대로
    tok = SITE_NAME_OFF.set(True)
    try:
        off = plan_calls([t, s], ["adverse", "outcome", "indication"])
    finally:
        SITE_NAME_OFF.reset(tok)
    assert all(not c.request.get("filters") for c in _facets(off, "adverse") + _facets(off, "outcome"))
    assert len(_facets(off, "adverse")) == 1


def _line(key, kinds, holder=None, role="하위 케이스", row=None, silver_table="adverse_risk"):
    layer = {"role": role, "holder": holder} if holder else None
    return fr.Line(key=key, kinds=kinds, layer=layer, silver_table=silver_table, silver_id=key, row=row or {})


def _ctx(**kw):
    base = dict(targets=set(), question="턱에 보톡스 맞아도돼 ?", md={"procedure_case": {"CAS-009"}, "procedure": {"PRC-001"},
                                                                      "product": {"PRD-022"}}, g2={"adverse", "indication"})
    base.update(kw)
    return fr.Ctx(**base)


def test_hf2_case_holder_site_family():
    jaw = {"SITE-072", "SITE-007", "SITE-076", "SITE-077"}  # 턱 가족(자기 · 조상 · 자손)
    ctx = _ctx(topic_sites=jaw, case_site={"CAS-009": "SITE-076", "CAS-020": "SITE-105"})
    lines = [
        _line("a", ["adverse"], {"holder_table": "procedure_case", "holder_code": "CAS-009", "holder_name": "사각턱보톡스"}),
        _line("b", ["adverse"], {"holder_table": "procedure_case", "holder_code": "CAS-020", "holder_name": "보톡스(종아리)"}),
        _line("c", ["adverse"], {"holder_table": "modality", "holder_code": "M1", "holder_name": "보톡스"}, role="자기"),
        _line("d", ["adverse"], {"holder_table": "procedure_case", "holder_code": "CAS-777", "holder_name": "모름"}),
    ]
    got = fr.decide(lines, fr.Plan(), ctx)
    assert got["a"] == (fr.FULL, "safety")  # 턱 가족 케이스 · 안전 = 상세
    assert got["b"] == (fr.NAMES, "site_other")  # 다른 부위 케이스 · 안전이라도 이름만
    assert got["c"][0] == fr.FULL  # 부위 없는 holder(계열 · 말 속 이름 「보톡스」) = 늘 상세
    assert got["d"][0] == fr.FULL  # 부위 모르는 케이스는 지금처럼
    # 명시 대상 케이스는 G3 으로 늘 상세 · 주제 부위가 없으면 거르지 않음
    ctx2 = _ctx(targets={"CAS-020"}, topic_sites=jaw, case_site={"CAS-020": "SITE-105"})
    assert fr.decide([lines[1]], fr.Plan(), ctx2)["b"] == (fr.FULL, "explicit")
    ctx3 = _ctx(case_site={"CAS-020": "SITE-105"})
    assert fr.decide([lines[1]], fr.Plan(), ctx3)["b"][0] == fr.FULL  # 주제 부위 없음 → 지금처럼(이름 일치 명시)


def test_hn1_owner_from_target_columns_for_layerless_rows():
    ln = _line("o", ["outcome"], None, row={"target_table": "product", "target_code": "PRD-420", "target_name": "인모드"},
               silver_table="outcome")
    assert fr.owner(ln) == ("product:PRD-420", "인모드", None)
    got = fr.decide([ln], fr.Plan(), _ctx(g2={"outcome"}, question="얼굴 타이트닝에는 어떤 시술들이 잇어 ?"))
    assert got["o"] == (fr.NAMES, "branch")  # 명동점 밖 대상 → 이름만(DQ1)
    ln2 = _line("p", ["outcome"], None, row={"target_table": "product", "target_code": "PRD-022", "target_name": "하이톡스"},
                silver_table="outcome")
    assert fr.decide([ln2], fr.Plan(), _ctx(g2={"outcome"}))["p"] == (fr.FULL, "read")


def test_hk1_safety_always_when_target_present():
    e = [Entry("procedure", "ZZP-1", "가짜시술", "target")]
    k = kp.decide(e, ["indication", "outcome"], {})
    assert "adverse" not in k.kinds
    safety_always(k, e)
    assert {"adverse", "contraindication", "recovery"} <= set(k.kinds)
    assert {k.by[c] for c in ("adverse", "contraindication", "recovery")} == {"safety_always"}
    assert "adverse" not in k.required  # 필수 아님
    c = [Entry("concern", "ZZC-1", "가짜고민", "target")]
    kc = kp.decide(c, ["indication"], {})
    before = list(kc.kinds)
    safety_always(kc, c)
    assert kc.kinds == before  # 대상이 고민뿐이면 그대로


def test_kos_block_together_uses_understand_kinds_not_words():
    from clinic_chat.consult import kos_block as kb

    assert kb.TOGETHER_KINDS == frozenset({"combo", "same_day"})
    assert SimpleNamespace(kinds=["combo"]).kinds[0] in kb.TOGETHER_KINDS


def test_hf2_case_site_from_mapping_first_and_hub_calls_counted():
    """검수 P2-5 — 명동점 케이스는 매핑 `hub.case_codes` → 매핑 부위(허브 GET 0) · 매핑 밖 케이스만 허브 profile GET(수를 셈)."""
    import asyncio

    from clinic_chat.consult import turn as turn_mod
    from clinic_chat.consult.turn import Consult

    calls = []

    class Hub:
        async def read_all(self, tool, req):
            calls.append((tool, req["node"]))
            return SimpleNamespace(complete=True, items=[{"subject_code": req["node"].split(":")[1], "site_name": "종아리"}])

    kos = SimpleNamespace(
        items=[{"hub": {"case_codes": ["CAS-009"], "site_codes": ["SITE-076"]}}],
        sites={"SITE-076": {"name": "사각턱"}, "SITE-105": {"name": "종아리"}},
    )
    lines = [{"layer": {"holder": {"holder_table": "procedure_case", "holder_code": c}}} for c in ("CAS-009", "CAS-020")]
    me = SimpleNamespace(ctx=SimpleNamespace(kos=kos), dec=None, _cur_topic=lambda: {"conditions": {"site": [{"code": "SITE-072"}]}},
                         el=SimpleNamespace(gold_hash="test-gold-p25"), d=SimpleNamespace(hub=Hub()),
                         case_sites={}, case_site_calls=0, case_site_mapped=0)
    turn_mod._CASE_SITE.pop(("test-gold-p25", "CAS-020"), None)
    asyncio.run(Consult.prep_case_sites(me, SimpleNamespace(material=SimpleNamespace(lines=lines))))
    assert me.case_sites == {"CAS-009": "SITE-076", "CAS-020": "SITE-105"}
    assert calls == [("facets", "procedure_case:CAS-020")] and me.case_site_mapped == 1 and me.case_site_calls == 1
    ctx = _ctx(topic_sites={"SITE-072", "SITE-076", "SITE-007"}, case_site=me.case_sites)
    got = fr.decide([_line("a", ["adverse"], {"holder_table": "procedure_case", "holder_code": "CAS-009", "holder_name": "x"}),
                     _line("b", ["adverse"], {"holder_table": "procedure_case", "holder_code": "CAS-020", "holder_name": "y"})],
                    fr.Plan(), ctx)
    assert got["a"][0] == fr.FULL and got["b"] == (fr.NAMES, "site_other")
