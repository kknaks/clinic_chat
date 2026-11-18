"""WP-3 — 객체 기준 조회(S5-T8 · QY6 · 검수 W-7 · 코디 W-9 · W-1 (가)) — 가짜 셋 · 켠 상태 명시."""

from __future__ import annotations

import asyncio

from clinic_chat.consult import plan
from clinic_chat.consult import state as st
from clinic_chat.walk.calls import Entry, plan_calls
from tests.state_fixture import Run, state_hub
from tests.test_state_object import apply
from tests.v04_fixture import set_out

SAFETY = ("adverse", "adverse_detail", "contraindication", "avoid")


def _qs(row: dict) -> list[dict]:
    return row["consult"]["queries"]


def test_s5_t8_walk_overlap_qy3_qy4_and_no_safety(tmp_path, fake_cli):
    route = fake_cli[1]
    r = Run(tmp_path, fake_cli)
    set_out(route, [("concern", "ZZC-1")], kinds=["indication"])
    row1, _, _ = r.turn("가짜고민 시술")
    q1 = [q for q in _qs(row1) if q["rule"] == "QY1"]
    assert q1 and all(q["skipped"] == "walk" for q in q1)  # 답 재료 조회가 읽은 조회는 다시 안 부른다(QM5)
    r.close()
    r = Run(tmp_path / "b", fake_cli)
    set_out(route, [("product", "ZZR-1")], kinds=["overview"])
    row, _, _ = r.turn("가짜제품 받고 싶어요")
    assert {q["rule"] for q in _qs(row)} == {"QY2", "QY3"}  # 대상만 → QY3 · 시너지 없음(고민이 없다 — U5-2 (a))
    set_out(route, [("concern", "ZZC-1"), ("product", "ZZR-1")], kinds=["indication"])
    row2, _, _ = r.turn("가짜고민 때문에요")
    rules = {q["rule"] for q in _qs(row2)}
    assert {"QY4", "QY4b"} <= rules and "QY3" not in rules  # 둘 다 있으면 QY4
    for rr in (row1, row, row2):
        for q in _qs(rr):
            assert (q["tool"], q["request"].get("facet")) in plan.ALLOWED  # QY6 — 닫힌 목록
            assert q["request"].get("facet") not in SAFETY
    r.close()


def test_w9_wishes_only_turn_makes_no_query():
    obj = st.empty()
    apply(obj, [], 1, kinds=["recovery"])
    assert obj["wishes"] and obj["wishes"][0]["kind"] == "recovery"
    hub = state_hub()

    async def go():
        r = plan.Reader(hub.client(page_limit=50), {}, [], [])
        await plan.run(obj, r)
        return r
    r = asyncio.run(go())
    assert r.queries == [] and hub.requests == []


def test_w7_query_key_equals_walk_call_key():
    """부위 거르개 값은 걷기와 같은 꼴(이름 글자) — `plan` 의 `Call.key()` 가 걷기의 것과 같다."""
    obj = st.empty()
    apply(obj, [("concern", "ZZC-9"), ("site", "ZZS-9")], 1)
    ents = [Entry("concern", "ZZC-9", "가짜팔자", "target", span="가짜팔자", start=0),
            Entry("site", "ZZS-9", "가짜팔자", "target", span="가짜팔자", start=0)]
    walk_keys = {c.key() for c in plan_calls(ents, ["indication"]) if c.tool == "concern_candidates"}
    assert {c.key() for c in plan.qy1_calls(obj, ["가짜팔자"])} == walk_keys


def test_w1_site_only_gives_concern_options():
    """QY7 → 0.4 QS6 — 선택지 = `concern_site` 로 묶인 고민(먼저) ∪ `facets(site, indication)` 행의 고민 · 같은 고민 한 번."""
    obj = st.empty()
    apply(obj, [("site", "ZZS-1")], 1)
    hub = state_hub()

    async def bound(code):
        return [{"table": "concern", "code": c, "name": "", "layer": "", "holder": "", "rank": -1, "first": 0,
                 "gids": [], "rows": 0} for c in ("ZZC-3", "ZZC-1")]

    async def go():
        r = plan.Reader(hub.client(page_limit=50), {}, [], [])
        return await plan.run(obj, r, site_options=bound), r
    p, r = asyncio.run(go())
    assert [q["rule"] for q in r.queries] == ["QY7"]
    assert [o["code"] for o in p.options["site:ZZS-1"]] == ["ZZC-3", "ZZC-1"]


def test_concern_options_layer_order():
    rows = [{"concern_code": "C9", "concern_name": "x", "gid": "g1",
             "layer": {"role": "물려받음", "holder": {"holder_table": "modality", "holder_name": "m"}}},
            {"concern_code": "C1", "concern_name": "y", "gid": "g2",
             "layer": {"role": "물려받음", "holder": {"holder_table": "procedure_case", "holder_name": "k"}}},
            {"concern_code": "C5", "concern_name": "z", "gid": "g3", "layer": {"role": "자기", "holder": {}}},
            {"concern_code": "C9", "concern_name": "x", "gid": "g4",
             "layer": {"role": "물려받음", "holder": {"holder_table": "procedure", "holder_name": "p"}}}]
    out = plan.concern_options(rows)
    assert [o["code"] for o in out] == ["C5", "C1", "C9"] and out[2]["holder"] == "p" and out[2]["rows"] == 2
