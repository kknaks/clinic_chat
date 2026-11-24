"""SPEC-010 WP-5 — 최저가 조합(§6-2 RC1 ~ RC10 · CO1 ~ CO4 · §6-3). 외부 호출 0(손으로 지은 작은 `$J` + 실물 v3 는 있으면)."""

from __future__ import annotations

import json

import pytest

from clinic_chat.config import ROOT
from clinic_chat.consult import kos_map as km
from clinic_chat.consult import price as pr

REAL = ROOT / "data" / "kos" / "myeongdong-mapping-20261120-v3.json"
need_real = pytest.mark.skipif(not REAL.is_file(), reason="실물 $J(git 무시) 없음")


def item(code, refs):
    return {
        "kos_item_code": code,
        "kos_item_name": "이름" + code,
        "kos_procedure": "묶음",
        "kos_machine": "",
        "kos_medicine": "",
        "kos_status": "PUBLISHED",
        "site_tiers": {"t1": "얼굴"},
        "site_scope": "얼굴 전체",
        "status": "매핑됨",
        "note": "",
        "options": [],
        "hub": {"procedure_codes": ["P"], "case_codes": ["C"], "site_codes": ["S1"], "products": []},
        "option_refs": [{"product_key": p, "option_key": o} for p, o in refs],
    }


def opt(key, title, typ, count, price, ev, covers, comps=None, closed=None):
    o = {
        "option_key": key,
        "title": title,
        "type": typ,
        "count": count,
        "price_amount": price,
        "event_price": ev,
        "additional_prices": [],
        "closed": closed,
        "covers": covers,
    }
    if comps is not None:
        o["compositions"] = [
            {
                "item_code": c,
                "count": n,
                "price": 1,
                "additional_prices": [{"visitor_type": "이벤트가", "amount": ev}],
                "comp_event_price_is_option_total": True,
            }
            for c, n in comps
        ]
    return o


def prod(key, title, options, *, closed=False, cats=(), event=False):
    return {
        "product_key": key,
        "title": title,
        "category_keys": list(cats),
        "is_event": event,
        "closed": closed,
        "order": 0,
        "options": options,
    }


PRODUCTS = [
    prod(
        "PA",
        "가짜 정가",
        [opt("A1", "1회", "single", 1, 1000, None, ["X1"]), opt("A5", "5회", "single", 5, 4000, None, ["X1"])],
    ),
    prod("PB", "[첫방문] 가짜", [opt("B1", "첫 1회", "single", 1, 800, 500, ["X1"])]),  # CO3 — 둠
    prod("PC", "[DB 블랙 1년권] 가입비", [opt("C1", "포함 1회", "single", 1, 0, None, ["X1"])]),  # CO1 + CO2
    prod("PD", "가짜 1년권", [opt("D1", "회원 1회", "single", 1, 300, None, ["X1"])]),  # CO2
    prod("PZ", "가짜 영원", [opt("Z1", "공짜 1회", "single", 1, 0, None, ["X1"])]),  # CO1 만
    prod(
        "PE",
        "가짜 남성제모",
        [opt("E1", "남 1회", "single", 1, 200, None, ["X3"]), opt("E2", "남 1회b", "single", 1, 150, None, ["X3"])],
    ),
    prod("PF", "가짜 여성제모", [opt("F1", "여 1회", "single", 1, 100, None, ["X3"])]),
    prod(
        "PG",
        "[이벤트] 가짜 묶음",
        [opt("G1", "X1+X2", "group", None, 2000, 1500, ["X1", "X2"], [("X1", 1), ("X2", 1)])],
        event=True,
    ),
    prod("PH", "가짜 밖 묶음", [opt("H1", "X1+X5", "group", None, 10, None, ["X1", "X5"], [("X1", 1), ("X5", 1)])]),
    prod("PI", "가짜 둘", [opt("I1", "X2 1회", "single", 1, 900, None, ["X2"])]),
    prod("PJ", "가짜 종료", [opt("J1", "X2 1회 싸게", "single", 1, 1, None, ["X2"])], closed=True),
    prod(
        "PK",
        "가짜 5회 묶음",
        [opt("K1", "X2 5회 + X4", "group", None, 3000, None, ["X2", "X4"], [("X2", 5), ("X4", 1)])],
    ),
    prod("PL", "가짜 X2 5회", [opt("L1", "X2 5회", "single", 5, 2500, None, ["X2"])]),
]


def kos(products=None) -> km.KosMap:
    ps = products or PRODUCTS
    refs: dict[str, list] = {}
    for p in ps:
        for o in p["options"]:
            for c in o["covers"]:
                refs.setdefault(c, []).append((p["product_key"], o["option_key"]))
    codes = ["X1", "X2", "X3", "X4", "X5", "X6"]
    d = {
        "meta": {"version": "v3", "hub_gold_hash": "g", "made_at": "t"},
        "items": [item(c, refs.get(c, [])) for c in codes],
        "groups": [{"kos_procedure": "묶음", "items": codes}],
        "sites": {
            "S1": {"name": "얼굴", "parent_code": "", "region": "face", "shape": "point", "points": [[0.5, 0.5]]}
        },
        "product_categories": [{"category_key": "C16", "name": "10월 첫방문 이벤트"}],
        "products": ps,
    }
    return km.parse(json.dumps(d, ensure_ascii=False).encode(), expect_items=None)


def keys(card):
    return [tuple(k) for k in card["keys"]]


def test_rc4_used_price_event_first():
    assert (
        pr.used({"price_amount": 1000, "event_price": 500}) == 500
        and pr.used({"price_amount": 1000, "event_price": None}) == 1000
    )


def test_co_rules_each_counted():
    m = kos()
    assert pr.co_rules(m, ("PC", "C1")) == ["CO1", "CO2"] and pr.co_rules(m, ("PD", "D1")) == ["CO2"]
    assert pr.co_rules(m, ("PZ", "Z1")) == ["CO1"] and pr.co_rules(m, ("PB", "B1")) == ["CO3"]
    assert pr.co_counts(m) == {"CO1": 2, "CO2": 2, "CO3": 1, "CO1&CO2": 1, "drop_union": 3}


def test_rc2_co_filters_other_singles_but_not_chosen():
    m = kos()
    r = pr.recommend(m, [{"code": "X1", "lines": [["PA", "A1"]]}])
    c1 = r["cards"][0]
    assert (
        keys(c1) == [("PB", "B1")] and c1["total"] == 500 and c1["list_total"] == 800 and c1["event"]
    )  # CO3 둠 · 이벤트가
    assert keys(r["cards"][1]) == [("PA", "A1")]  # 0 원 · 회원권(CO1 · CO2) 후보 0
    assert r["filtered"] == {"CO1": 2, "CO2": 2, "CO3": 1}  # 규칙마다 따로(겹침 포함)
    r2 = pr.recommend(m, [{"code": "X1", "lines": [["PC", "C1"]]}])  # C9d — 손님이 회원권 옵션을 직접 고름
    assert keys(r2["cards"][0]) == [("PC", "C1")] and r2["cards"][0]["total"] == 0


def test_rc2_count_kept():
    m = kos()
    r = pr.recommend(m, [{"code": "X1", "lines": [["PA", "A5"]]}])
    assert [keys(c) for c in r["cards"]] == [[("PA", "A5")]]  # 5 회 줄은 1 회 옵션으로 덮지 않음


def test_rc2_gender_word():
    m = kos()
    r = pr.recommend(m, [{"code": "X3", "lines": [["PE", "E1"]]}])  # C9b — 남성 → 여성(100) 후보 0
    assert [keys(c) for c in r["cards"]] == [[("PE", "E2")], [("PE", "E1")]]
    r2 = pr.recommend(m, [{"code": "X3", "lines": [["PF", "F1"]]}])
    assert [keys(c) for c in r2["cards"]] == [[("PF", "F1")]]


def test_rc2_group_needs_all_comps_lines_and_no_outside():
    m = kos()
    both = [{"code": "X1", "lines": [["PA", "A1"]]}, {"code": "X2", "lines": [["PI", "I1"]]}]
    r = pr.recommend(m, both)
    assert keys(r["cards"][0]) == [("PB", "B1"), ("PI", "I1")] and r["cards"][0]["total"] == 1400
    assert (
        keys(r["cards"][1]) == [("PG", "G1")] and r["cards"][1]["total"] == 1500
    )  # group 이벤트가(구성 반복 값 안 씀)
    assert r["cards"][1]["options"][0]["covers"] == ["이름X1", "이름X2"] and r["cards"][1]["list_total"] == 2000
    no_line = [{"code": "X1", "lines": [["PA", "A1"]]}, {"code": "X2", "lines": []}]  # (가) 구성 진료항목 줄 없음
    r2 = pr.recommend(m, no_line)
    assert all(("PG", "G1") not in keys(c) for c in r2["cards"]) and r2["no_pick"] == ["X2"]
    wrong = [{"code": "X2", "lines": [["PI", "I1"]]}, {"code": "X4", "lines": []}]  # (나) 줄 count 1 ≠ 구성 5
    assert all(("PK", "K1") not in keys(c) for c in pr.recommend(m, wrong)["cards"])
    out = [{"code": "X1", "lines": [["PA", "A1"]]}]  # 장바구니 밖 구성(X5) 품은 H1 은 후보 아님
    assert all(("PH", "H1") not in keys(c) for c in pr.recommend(m, out)["cards"])


def test_rc3_two_same_lines_and_overlap():
    m = kos()
    r = pr.recommend(m, [{"code": "X1", "lines": [["PA", "A1"], ["PA", "A1"]]}])
    assert (
        keys(r["cards"][0]) == [("PB", "B1"), ("PB", "B1")] and r["cards"][0]["total"] == 1000
    )  # 같은 (c, n) 줄 둘 다 덮음
    for c in r["cards"]:
        assert len(c["options"]) == 2  # 겹침 0 · 빠짐 0


def test_rc6_no_price_and_op6_no_pick():
    m = kos()
    r = pr.recommend(m, [{"code": "X4", "lines": []}, {"code": "X6", "lines": []}, {"code": "X2", "lines": []}])
    assert r["no_price"] == ["X4", "X6"] and r["no_pick"] == ["X2"] and r["cards"] == []  # group 만 · 옵션 0 · 줄 없음


def test_rc10_closed_chosen_becomes_no_pick():
    ps = json.loads(json.dumps(PRODUCTS))
    m0 = kos(ps)
    for p in ps:
        if p["product_key"] == "PI":
            p["options"][0]["closed"] = True  # 고정본에서 고른 옵션을 판매 종료로(실물엔 0)
    ps.append(prod("PM", "가짜 X2 남음", [opt("M1", "X2", "single", 1, 50, None, ["X2"])]))
    m = kos(ps)
    assert ("PI", "I1") in pr.single_options(m0, "X2") and ("PI", "I1") not in pr.single_options(m, "X2")
    r = pr.recommend(m, [{"code": "X2", "lines": [["PI", "I1"]]}])
    assert r["no_pick"] == ["X2"] and r["closed_lines"] == [{"code": "X2", "key": ["PI", "I1"]}] and r["cards"] == []


def test_closed_others_excluded_and_rc7_tie():
    m = kos()
    r = pr.recommend(m, [{"code": "X2", "lines": [["PI", "I1"]]}])
    assert all(("PJ", "J1") not in keys(c) for c in r["cards"])  # 판매 종료 상품 뺌
    ps = PRODUCTS + [prod("PN", "가짜 같은값", [opt("N1", "X2 1회 같은값", "single", 1, 900, None, ["X2"])])]
    r2 = pr.recommend(kos(ps), [{"code": "X2", "lines": [["PN", "N1"]]}])
    assert [keys(c) for c in r2["cards"]] == [[("PI", "I1")], [("PN", "N1")]]  # 동점 → `$J` 순서


def test_rc9_caps():
    m = kos()
    many = [{"code": "X1", "lines": [["PA", "A1"]] * 11}]
    r = pr.recommend(m, many)
    assert r["capped"] and len(r["cards"]) == 1 and keys(r["cards"][0]) == [("PA", "A1")] * 11
    r2 = pr.recommend(m, [{"code": "X1", "lines": [["PA", "A1"]] * 3}], combos_max=5)
    assert r2["capped"] and len(r2["cards"]) == 1


def test_same_group_key_counted_once():
    m = kos()
    r = pr.recommend(m, [{"code": "X1", "lines": [["PA", "A1"]]}, {"code": "X2", "lines": [["PI", "I1"]]}])
    g = [c for c in r["cards"] if ("PG", "G1") in keys(c)][0]
    assert keys(g) == [("PG", "G1")] and g["total"] == 1500  # 두 진료항목을 덮어도 옵션 한 번 · 한 번 셈


# ---------- 실물 v3 ----------


@need_real
def test_real_co_counts():
    assert pr.co_counts(km.load(REAL)) == {"CO1": 13, "CO2": 22, "CO3": 23, "CO1&CO2": 10, "drop_union": 25}


@need_real
def test_real_onda_titanium_group():
    m = km.load(REAL)
    r = pr.recommend(
        m,
        [
            {"code": "26-DRPTP-OND-50KJ", "lines": [["P121", "P121-6"]]},
            {"code": "26-TTNLF-SPRNT-30KJ", "lines": [["P136", "P136-3"]]},
        ],
    )
    c1, c2 = r["cards"]
    assert keys(c1) == [("P015", "P015-1")] and c1["total"] == 590000 and c1["list_total"] == 1100000
    assert keys(c2) == [("P121", "P121-6"), ("P081", "P081-1")] and c2["total"] == 630000  # 첫방문 티타늄(CO3 둠)


@need_real
def test_real_c9_c9b_c9c():
    m = km.load(REAL)
    r = pr.recommend(m, [{"code": "26-Avalanche-1", "lines": [["P113", "P113-3"], ["P113", "P113-4"]]}])
    assert r["cards"][0]["total"] == 200000 and len(r["cards"][0]["options"]) == 2  # C9 줄 둘 각각 같은 회차
    r2 = pr.recommend(m, [{"code": "26-Avalanche-1", "lines": [["P112", "P112-4"]]}])
    assert [keys(c) for c in r2["cards"]] == [[("P112", "P112-4")]]  # C9b 여성 50,000 후보 0
    r3 = pr.recommend(m, [{"code": "26-THM3-600SHOT", "lines": [["P111", "P111-2"]]}])
    assert [keys(c) for c in r3["cards"]] == [[("P111", "P111-2")]] and r3["filtered"][
        "CO1"
    ] == 1  # C9c 가입비 · 회원가 빠짐


def test_chosen_option_must_cover_item():
    m = kos()
    r = pr.recommend(m, [{"code": "X2", "lines": [["PA", "A1"]]}])  # X1 의 옵션을 X2 줄로
    assert r["cards"] == [] and r["no_pick"] == ["X2"] and r["closed_lines"] == [{"code": "X2", "key": ["PA", "A1"]}]
