"""WP-3 1 ~ 3 — 조회 계획 = 대상 × 읽기로 한 종류(`plan_calls` · BR1 ~ BR4 · §3-2a). Jev · LLM · 허브 없이."""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from clinic_chat.walk.calls import Call, Entry, followup_calls, key_calls, plan_calls, range_kinds
from clinic_chat.walk.kinds import KIND_CODES, KINDS, LLM_COLUMNS, RECORD_ONLY, range_of
from clinic_chat.walk.ranges import BY_CODE, ROLES_TARGET

P = Entry("procedure", "ZZP-1", "가짜시술")
P2 = Entry("procedure", "ZZP-2", "가짜시술2")
PR = Entry("product", "ZZR-1", "가짜제품")
A = Entry("adverse", "ZZA-1", "가짜사건")
CND = Entry("condition", "ZZN-1", "가짜조건")
C = Entry("concern", "ZZC-1", "가짜고민")
S = Entry("site", "ZZS-1", "가짜부위")


def spec(calls):
    return [(c.tool, c.request.get("facet"), tuple(c.request.get("roles") or ()), tuple(c.request.get("classes") or ()),
             tuple(c.request.get("package_kinds") or ())) for c in calls]


def test_fourteen_kinds_eight_ranges():
    assert len(KINDS) == 14 and len(BY_CODE) == 8 and BY_CODE["procedure"].depth is None


def test_only_read_kinds_are_called():
    """「폭」 칸에 있어도 읽기로 하지 않은 종류의 조회는 내지 않는다."""
    assert spec(plan_calls([P], ["overview"])) == [("facets", "profile", ROLES_TARGET, (), ())]
    got = spec(plan_calls([P], ["outcome"]))
    assert got == [("facets", "outcome", ROLES_TARGET, (), ())]
    assert plan_calls([P], []) == []


def test_overview_and_mechanism_share_one_profile_call():
    calls = plan_calls([P], ["overview", "mechanism"])
    assert len(calls) == 1 and calls[0].kinds == ("overview", "mechanism")


def test_target_roles_include_lower_layers_and_product_offer():
    for c in plan_calls([P], [k for k in KIND_CODES if k != "lineup"]):
        if c.tool == "facets":
            assert c.request["roles"] == list(ROLES_TARGET)
    off = plan_calls([PR], ["offer"])[0]
    assert off.request["roles"] == ["자기", "하위 제품"] and "물려받음" not in off.request["roles"]
    assert spec(plan_calls([P], ["lineup"])) == [("scope", None, (), (), ())]


def test_pair_kinds_and_q25_classes():
    assert spec(plan_calls([P], ["order_interval"])) == [("pair", None, (), ("순서", "간격", "재시술 간격"), ())]
    assert spec(plan_calls([P, P2], ["same_day"])) == [("pair", None, (), (), ("같은 날 조합",))]
    assert spec(plan_calls([P], ["combo"])) == [("pair", None, (), ("보완",), ())]
    assert spec(plan_calls([P], ["avoid"])) == [("pair", None, (), ("회피",), ())]


def test_filters_only_from_entries_and_no_other_narrowing():
    adv = [c for c in plan_calls([P, A], ["adverse"]) if c.request.get("facet") == "adverse"][0]
    assert adv.request["filters"] == {"adverse_code": "ZZA-1"}
    ct = plan_calls([P, CND], ["contraindication"])[0]
    assert ct.request["filters"] == {"condition_code": "ZZN-1"}
    cc = plan_calls([C, S], ["indication"])[0]
    assert cc.tool == "concern_candidates" and cc.request["site"] == "가짜부위"
    for c in plan_calls([P, P2], KIND_CODES):
        assert not {"self_pair", "same_day_only", "hier"} & set(c.request)
    assert all("filters" not in c.request for c in plan_calls([P], KIND_CODES))
    assert spec(plan_calls([A], ["adverse"])) == [("facets", "adverse", (), (), ())]  # 값 쪽 시작


def _attr(table, kinds):
    return [c.request["facet"] for c in plan_calls([Entry(table, "ZZX-1")], kinds)]


def test_attr_start_reads_only_read_kinds_facets():
    """§3-2 속성 시작 — 개요 · 적응(기본) + 읽기로 한 종류의 맞춤 facet 만(효과도 outcome 일 때만 — R2-N2)."""
    base = ["overview", "indication"]
    assert {t: _attr(t, base) for t in ("material", "method", "anatomy", "site")} == {
        "material": ["profile"], "method": ["profile"], "anatomy": ["profile"], "site": ["profile", "indication"]}
    assert _attr("site", [*base, "outcome"]) == ["profile", "indication", "outcome"]
    assert _attr("site", [*base, "adverse"]) == ["profile", "indication", "adverse", "adverse_detail"]
    assert _attr("material", [*base, "offer"]) == ["profile", "offer"]
    assert {t: len(_attr(t, KIND_CODES)) for t in ("material", "method", "anatomy", "site")} == {
        "material": 2, "method": 1, "anatomy": 2, "site": 7}


def test_offer_name_path_adds_and_keeps_attr_start():
    on = Entry("offer_name", "", "가짜")
    calls = plan_calls([Entry("material", "ZZM-1"), on], ["overview", "indication", "offer"])
    assert [c.request.get("facet") for c in calls] == ["profile", "offer", "offer"]
    assert calls[-1].request == {"facet": "offer", "offer_name_contains": "가짜"}
    assert not [c for c in plan_calls([Entry("material", "ZZM-1"), on], ["overview"]) if "offer_name_contains" in c.request]


def test_concern_followup_profile_per_group_and_key_calls():
    cc = Call("concern_candidates", {"concern_code": "ZZC-1"}, ("indication",), ("concern",))
    resp = {"groups": [{"candidate_table": "procedure_case", "candidate_code": "ZZK-1"},
                       {"candidate_table": "procedure", "candidate_code": "ZZP-1"}]}
    f = followup_calls(cc, resp)
    assert [(c.request["node"], tuple(c.request["roles"])) for c in f] == [
        ("procedure_case:ZZK-1", ("자기", "물려받음")), ("procedure:ZZP-1", ("자기", "물려받음"))]
    assert all(c.meta["followup"] and c.kinds == ("indication",) for c in f)
    assert [c.request for c in key_calls(plan_calls([P], KIND_CODES)) if c.tool == "scope"] == [{"node": P.node}]  # K6 — lineup 만


def test_range_kinds_for_end_state_judgement():
    assert set(range_kinds([C], ["concern"])) == {"indication", "package"}
    assert set(range_kinds([P], ["procedure"])) == {"overview", "mechanism", "indication", "outcome", "lineup"}
    assert range_of("recovery", "concern", False) is None  # 고민 대상 × 회복 = 「—」(KD4 건너뜀)


def test_b_and_key_script_share_plan_calls():
    """WP-3 완료 5 — B 와 답 키 스크립트가 같은 객체를 import."""
    from clinic_chat.columns import col_b
    from clinic_chat.evalset import key_calls as kc

    assert col_b.plan_calls is plan_calls and kc.plan_calls is plan_calls


def test_llm_columns_have_no_record_only_columns():
    for cols in LLM_COLUMNS.values():
        assert not set(cols) & set(RECORD_ONLY) and not any(c.endswith("_code") for c in cols)
    assert len(LLM_COLUMNS) == 13


@pytest.mark.skipif(not os.environ.get("GOLD_CSV_DIR"), reason="데이터 env 없음")
def test_llm_columns_exist_in_gold_headers():
    """§3-2a 13 줄의 칸이 gold CSV 헤더에 다 있다(헤더에 없는 칸 0)."""
    d = Path(os.environ["GOLD_CSV_DIR"])
    for (table, _), cols in LLM_COLUMNS.items():
        with (d / f"{table}.csv").open(encoding="utf-8-sig") as f:
            head = next(csv.reader(f))
        assert not set(cols) - set(head), (table, set(cols) - set(head))
