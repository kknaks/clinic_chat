"""테스트 고침 E(사용자 11-23 「지금 나온 거 고치자」) — 주제 머리 · 주제 하나 머리 · 부위별 시술이면 주제 찢기 · 큰 재료 답 실패.

외부 호출 0(장난감 셈 · 가짜 CLI · 가짜 허브).

입력 전수 표(말 꼴 × 짝 → 기대):

| # | 말 꼴 | 예 | 기대 |
|---|---|---|---|
| S1 | 부위 둘 · 시술 둘 짝 | 눈가는 볼뉴머 입꼬리는 필러 | 찢기 — (눈 · 눈가 고민 · 볼뉴머) / (입꼬리 · 입꼬리 고민 · 필러) |
| S2 | 부위 둘 · 시술 하나 | 눈가랑 입꼬리에 필러 | 안 찢음 — 한 주제에 필러 |
| S3 | 부위 둘 · 같은 시술 각각 | 눈가는 필러 입꼬리도 필러 | 안 찢음(짝 시술이 같음) |
| S4 | 부위 하나 · 시술 둘 | 입꼬리에 필러랑 보톡스 | 안 찢음 |
| S5 | 시술 먼저 · 부위 둘 | 필러를 눈가랑 입꼬리에 | 안 찢음(시술이 두 부위 모두에 걸림) |
| S6 | 짝 뒤 한 부위만 다시 | 입꼬리에는 필러를 맞고 싶다니까 | 입꼬리 주제 그대로(같은 값) · 눈가 주제 그대로 |
| S7 | 짝 뒤 한 부위 시술 바꿈 | 눈가는 필러로 | 눈가 주제로 되돌림 · 볼뉴머 → 필러 교체 · 입꼬리 주제 그대로 |
| S8 | 짝 뒤 넓히기 | 다른 시술도 보여줘 | 지금(입꼬리) 주제만 시술 비움 · 눈가 주제 그대로 |
| S9 | 짝 뒤 바로잡기 | 아니 ~ | 그 턴에 만든 주제 지움 · 찢긴 주제는 찢기 전으로 |
| S10 | 이해 호출 pairs 만 짝을 냄 | (규칙 차례로는 짝이 안 보임) | 이해 호출 짝으로 찢기 |
"""

from __future__ import annotations

import asyncio
import copy
import json

from clinic_chat.consult import items_view as iv
from clinic_chat.consult import material as mat
from clinic_chat.consult import topics as tp
from tests.state_fixture import Run, state_hub
from tests.test_v05_detect import EXTRA_ENTRY
from tests.test_v05_fix_a import SITE_ENTRY
from tests.v04_fixture import entry_rows, list_route

U = {
    "S-EYE": set(range(1, 21)),  # 눈 주변
    "S-MOU": set(range(11, 31)),  # 입꼬리
    "C-EYEW": set(range(1, 21)),  # 눈가주름
    "C-MOUW": set(range(11, 31)),  # 입꼬리주름
    "P-FIL": {1, 2, 3, 15, 16, 25, 26, 27},  # 필러
    "P-BTX": {4, 5, 17, 28},  # 보톡스
    "R-VOL": {6, 7, 18},  # 볼뉴머(제품)
    "P-RF": {6, 7, 18, 40},  # 고주파(볼뉴머의 시술)
}


def V(code: str, **kw) -> dict:
    t = {"S": "site", "C": "concern", "P": "procedure", "R": "product"}[code[0]]
    return {"table": t, "code": code, "name": code, **kw}


async def toy(conds: dict) -> list[str]:
    sets = []
    for s in ("procedure", "site", "concern", "effect", "product"):
        if conds.get(s):
            sets.append(set().union(*[U.get(x["code"], set()) for x in conds[s]]))
    if not sets:
        return []
    inter = set.intersection(*sets)
    for x in conds.get("exclude") or []:
        inter -= U.get(x["code"], set())
    return [str(i) for i in sorted(inter)]


def step(topics, active, vals, n, *, text="", llm=None):
    turn, order = tp.turn_conditions(list(vals), [], None)
    return asyncio.run(tp.decide(topics, active, turn, order, n=n, llm=llm, withdrawn=[], count=toy, utterance=text))


def codes(t, slot):
    return [v["code"] for v in t["conditions"].get(slot) or []]


def by_id(d, tid):
    return next(t for t in d.topics if t["id"] == tid)


# ---------- 1 · 2 머리 ----------


def test_header_said_values_once_no_product_procedures():
    """conv 03b8 T4 꼴 — 이름 일치 둘 · 제품 풀이 시술(HIFU · 고주파) · 제품 셋."""
    c = {
        "site": [{"code": "SITE-007", "name": "얼굴"}],
        "concern": [{"code": "CON-1", "name": "피부 탄력 저하"}],
        "procedure": [
            {"table": "name", "code": "item:소프웨이브", "name": "소프웨이브"},
            {"table": "name", "code": "kos_procedure:소프웨이브", "name": "소프웨이브"},
            {"table": "procedure", "code": "PRC-311", "name": "HIFU", "from": "product"},
            {"table": "procedure", "code": "PRC-312", "name": "고주파", "from": "product"},
        ],
        "product": [
            {"code": "P1", "name": "소프웨이브"},
            {"code": "P2", "name": "써마지"},
            {"code": "P3", "name": "울쎄라"},
        ],
    }
    assert iv.cond_names(c) == ["얼굴", "피부 탄력 저하", "소프웨이브", "써마지", "울쎄라"]


# ---------- 3 찢기 — 입력 전수 표 ----------


def g(*vals):
    return tp.pair_groups(list(vals))


def test_s1_to_s5_pair_groups_and_should_split():
    assert tp.should_split(g(V("S-EYE"), V("R-VOL"), V("S-MOU"), V("P-FIL")))  # S1
    assert not tp.should_split(g(V("S-EYE"), V("S-MOU"), V("P-FIL")))  # S2
    assert not tp.should_split(g(V("S-EYE"), V("P-FIL"), V("S-MOU"), V("P-FIL")))  # S3
    assert not tp.should_split(g(V("S-MOU"), V("P-FIL"), V("P-BTX")))  # S4
    assert not tp.should_split(g(V("P-FIL"), V("S-EYE"), V("S-MOU")))  # S5
    gs = g(V("C-EYEW"), V("S-EYE"), V("R-VOL"), V("S-MOU"), V("P-FIL"))
    assert [(x["site"]["code"], [p["code"] for p in x["picks"]]) for x in gs] == [
        ("S-EYE", ["R-VOL"]),
        ("S-MOU", ["P-FIL"]),
    ]


def two_site_topic():
    d1 = step([], None, [V("S-EYE"), V("C-EYEW"), V("S-MOU"), V("C-MOUW")], 1)
    assert len(d1.topics) == 1 and codes(d1.topics[0], "site") == ["S-EYE", "S-MOU"]
    return d1


def _eye_turn():
    t = tp.turn_conditions([V("S-EYE"), V("R-VOL")], [], None)[0]
    t["procedure"].append(
        {"table": "procedure", "code": "P-RF", "name": "P-RF", "from": "product"}
    )  # turn.py 제품 풀이
    return t


SUBS = [
    {"turn": _eye_turn(), "site": "S-EYE", "keep_concern": [V("C-EYEW")]},
    {"turn": tp.turn_conditions([V("S-MOU"), V("P-FIL")], [], None)[0], "site": "S-MOU", "keep_concern": [V("C-MOUW")]},
]


def split_t2():
    d1 = two_site_topic()
    d2 = asyncio.run(tp.split_decide(d1.topics, d1.active, copy.deepcopy(SUBS), n=2, count=toy))
    return d1, d2


def test_s1_split_current_topic_into_two():
    _, d2 = split_t2()
    t1, t2 = by_id(d2, "t1"), by_id(d2, "t2")
    assert (codes(t1, "site"), codes(t1, "concern"), codes(t1, "product")) == (["S-EYE"], ["C-EYEW"], ["R-VOL"])
    assert (codes(t2, "site"), codes(t2, "concern"), codes(t2, "procedure")) == (["S-MOU"], ["C-MOUW"], ["P-FIL"])
    assert t1["items"] == ["6", "7", "18"] and t2["items"] == ["15", "16", "25", "26", "27"]  # 한 주제(앞 판)였으면 2
    assert d2.active == "t2" and d2.rec["move"] == "split" and [x["topic"] for x in d2.rec["split"]] == ["t1", "t2"]
    assert d2.rec["split"][0]["picks"] == ["R-VOL"]  # 제품 풀이 시술(고주파)은 기록 · 머리에 안 보임
    assert iv.cond_names(t1["conditions"]) == ["S-EYE", "C-EYEW", "R-VOL"]


def test_s6_same_site_pick_again_stays():
    _, d2 = split_t2()
    d3 = step(d2.topics, d2.active, [V("S-MOU"), V("P-FIL")], 3, text="입꼬리에는 필러를 맞고 싶다니까")
    assert d3.active == "t2" and d3.rec["move"] == "deepen" and codes(by_id(d3, "t2"), "procedure") == ["P-FIL"]
    assert codes(by_id(d3, "t1"), "product") == ["R-VOL"]  # 볼뉴머 안 사라짐(앞 판 T4 문제)


def test_s7_change_other_site_pick_returns_and_replaces():
    _, d2 = split_t2()
    d3 = step(d2.topics, d2.active, [V("S-EYE"), V("P-FIL")], 3, text="눈가는 필러로")
    t1 = by_id(d3, "t1")
    assert d3.active == "t1" and d3.rec["move"] == "return" and d3.rec["same_values"]
    assert codes(t1, "procedure") == ["P-FIL"] and not codes(t1, "product")  # 고르기 교체
    assert codes(by_id(d3, "t2"), "procedure") == ["P-FIL"] and codes(by_id(d3, "t2"), "site") == ["S-MOU"]


def test_s8_widen_after_split_clears_current_only():
    _, d2 = split_t2()
    d3 = step(d2.topics, d2.active, [], 3, text="다른 시술도 보여줘")
    assert d3.rec["widen"]["topic"] == "t2" and not codes(by_id(d3, "t2"), "procedure")
    assert codes(by_id(d3, "t1"), "product") == ["R-VOL"]


def test_s9_undo_after_split_restores_merged_topic():
    d1, d2 = split_t2()
    d3 = step(d2.topics, d2.active, [], 3, text="아니 그게 아니라")
    assert [t["id"] for t in d3.topics] == ["t1"] and d3.rec["undo"]["removed"]
    assert codes(d3.topics[0], "site") == ["S-EYE", "S-MOU"] and not codes(d3.topics[0], "product")  # 찢기 전


def test_split_without_multi_site_current_makes_new_or_same_value():
    d1 = step([], None, [V("S-EYE"), V("C-EYEW")], 1)  # 지금 주제가 부위 하나
    d2 = asyncio.run(tp.split_decide(d1.topics, d1.active, copy.deepcopy(SUBS), n=2, count=toy))
    assert codes(by_id(d2, "t1"), "product") == ["R-VOL"]  # 같은 값(눈) 주제에 깊게
    assert codes(by_id(d2, "t2"), "site") == ["S-MOU"] and d2.active == "t2"  # 입꼬리는 새 주제


# ---------- 4 큰 재료 ----------


def test_shrink_halves_largest_group_until_fits_and_prunes_new_lines():
    rows_a = [{"n": i, "text": "가" * 200} for i in range(1, 41)]
    rows_b = [{"n": i, "text": "나" * 20} for i in range(41, 46)]
    tm = mat.TurnMaterial(
        groups=[
            {"group": 1, "table": "g_indication", "rows": rows_a, "lines": [r["n"] for r in rows_a]},
            {"group": 2, "table": "g_profile", "rows": rows_b, "lines": [r["n"] for r in rows_b]},
        ],
        new_lines=[{"n": i} for i in range(1, 46)],
    )

    def fits(t):
        return sum(len(json.dumps(g["rows"], ensure_ascii=False).encode()) for g in t.groups) < 6000

    rec = mat.shrink(tm, fits)
    assert fits(tm) and rec["rounds"] >= 1 and rec["dropped_rows"] > 0
    a = tm.groups[0]
    assert a["rows"][0]["n"] == 1 and a["lines"] == [r["n"] for r in a["rows"]]  # 앞쪽 행이 남음
    assert len(tm.groups[1]["rows"]) == 5  # 작은 묶음은 그대로
    kept = {r["n"] for g in tm.groups for r in g["rows"]}
    assert {x["n"] for x in tm.new_lines} == kept  # 뺀 줄은 장부 new_lines 에서도 빠짐
    assert rec["by_table"]["g_indication"][0] == 40


# ---------- 끝까지(가짜 CLI) — 찢기 · 장부 · 머리 ----------


def understand_raw(targets, *, pairs=()) -> str:
    raw = json.dumps(
        {
            "targets": [{"table": t, "code": c, "sure": True} for t, c in targets],
            "kind": "new",
            "sites": [],
            "withdraw": [],
            "exclude": [],
            "pairs": list(pairs),
            "kinds": [],
            "out": False,
            "country": None,
            "topic": None,
        },
        ensure_ascii=False,
    )
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": raw,
            "duration_api_ms": 9,
            "usage": {"input_tokens": 3, "output_tokens": 20},
            "modelUsage": {"claude-test": {}},
        },
        ensure_ascii=False,
    )


def test_turn_split_end_to_end(tmp_path, fake_cli):
    hub = state_hub()
    hub.routes["resolve"] = list_route(entry_rows() + EXTRA_ENTRY + SITE_ENTRY)
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        (fake_cli[1] / "understand.json").write_text(
            understand_raw([("site", "ZS-072"), ("site", "ZS-057")]), encoding="utf-8"
        )
        row1, _, body1 = r.turn("가짜턱 가짜입")
        assert len(row1["consult"]["topics"]) == 1 and body1.count('<details class="cs-topic"') == 1  # 하나여도 머리
        (fake_cli[1] / "understand.json").write_text(
            understand_raw(
                [("site", "ZS-072"), ("procedure", "ZPRC-001"), ("site", "ZS-057"), ("procedure", "ZPRC-010")]
            ),
            encoding="utf-8",
        )
        row2, _, body2 = r.turn("가짜턱은 가짜보톡스시술 가짜입은 가짜리프팅시술")
        c = row2["consult"]
        assert c["topic"]["move"] == "split" and c["topic"]["pairs"]["from"] == "rule"
        ts = {t["id"]: t for t in c["topics"]}
        assert [v["code"] for v in ts["t1"]["conditions"]["site"]] == ["ZS-072"]
        assert [v["code"] for v in ts["t2"]["conditions"]["site"]] == ["ZS-057"]
        assert c["active_topic"] == "t2" and body2.count('<details class="cs-topic"') == 2  # 찢어진 주제 둘
    finally:
        r.close()


def test_s10_llm_pairs_used_when_rule_order_has_no_split(tmp_path, fake_cli):
    hub = state_hub()
    hub.routes["resolve"] = list_route(entry_rows() + EXTRA_ENTRY + SITE_ENTRY)
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        (fake_cli[1] / "understand.json").write_text(
            understand_raw([("site", "ZS-072"), ("site", "ZS-057")]), encoding="utf-8"
        )
        r.turn("가짜턱 가짜입")
        tg = [("procedure", "ZPRC-001"), ("procedure", "ZPRC-010"), ("site", "ZS-072"), ("site", "ZS-057")]
        pairs = [{"site": "ZS-072", "pick": "procedure:ZPRC-001"}, {"site": "ZS-057", "pick": "procedure:ZPRC-010"}]
        (fake_cli[1] / "understand.json").write_text(understand_raw(tg, pairs=pairs), encoding="utf-8")
        row2, _, _ = r.turn("가짜보톡스시술 가짜리프팅시술 각각 가짜턱 가짜입")
        assert row2["consult"]["topic"]["move"] == "split" and row2["consult"]["topic"]["pairs"]["from"] == "llm"
    finally:
        r.close()
