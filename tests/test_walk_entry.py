"""WP-1 · WP-2 8 — 입구 고르기(후보 0 일 때만) · 상품 이름 길(새 `matched` 꼴 — span · start · covered_by)."""

from __future__ import annotations

import pytest

from clinic_chat.walk.calls import plan_calls
from clinic_chat.walk.entry import offer_name_phrases
from clinic_chat.walk.front import front
from tests.conftest import make_settings
from tests.fake_jev import FakeJev
from tests.walk_fixture import P1, cand, jev_noul, make_hub


async def run(tmp_path, hub, noul):
    s = make_settings(tmp_path)
    return await front("가짜 질문", hub.client(), FakeJev(noul_fn=noul).jev(s), s)


@pytest.mark.parametrize("noul,gap,end,result", [
    (0.9, 0.1, None, "confirmed"), (0.5, 0.1, "입구 없음", "ask_back"),
    (0.1, 0.1, "입구 없음", "none"), (0.1, 0.9, "범위 밖", "none")])
async def test_entry_pick_only_when_zero_candidates(tmp_path, noul, gap, end, result):
    lst = [{"gold_table": "g_entry", "gid": f"GEN-9900{i}", **P1, "entity_code": f"ZZP-{i}"} for i in range(40)]
    f = await run(tmp_path, make_hub(resolve=[], list_mode=lst),
                  jev_noul(kinds={"overview": 0.9}, gap=gap, entry=lambda n: noul if n == "entry_3" else 0.1))
    assert f.end == end and f.trace_entry_pick["result"] == result
    assert f.trace_entry_pick["concepts"] == 40 and f.trace_entry_pick["requests"] == 2  # 32 묶음 → 전부
    if result == "confirmed":
        assert f.entries[0].by == "noul" and f.entries[0].read_as["by"] == "entry_pick"
        assert f.trace_entry["by"] == "noul"
    if result == "ask_back":
        assert f.ask_back and f.ask_back[0]["id"] == "procedure:ZZP-3"


def test_offer_name_phrases_use_norm_start():
    """조각의 `start`(허브 norm 자리 — 공백 뺀 글)로 질문 안 자리를 되짚어 이어 붙인다(사이가 공백뿐이면 한 구)."""
    q = "알파 베타 감마는 무엇이고 델타는?"
    cands = [{"matched": {"span": "베타감마", "start": 2}}, {"matched": {"span": "감마", "start": 4}},
             {"matched": {"span": "알파", "start": 0}}, {"matched": {"span": "델타", "start": 11}}]
    assert offer_name_phrases(q, cands) == ["알파 베타 감마", "델타"]


async def test_offer_kind_without_offer_start_entry_adds_offer_name(tmp_path):
    M = {"entity_table": "modality", "entity_code": "ZZD-1", "entity_name": "가짜계열", "text": "가짜계열"}
    f = await run(tmp_path, make_hub(resolve=[cand(M, span="질문", start=2)]), jev_noul(kinds={"offer": 0.9}))
    assert [e.name for e in f.entries if e.table == "offer_name"] == ["질문"]
    assert {"facet": "offer", "offer_name_contains": "질문"} in [c.request for c in plan_calls(f.entries, f.kinds)]
    f2 = await run(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(kinds={"offer": 0.9}))
    assert not [e for e in f2.entries if e.table == "offer_name"]


async def test_attr_entry_plus_offer_keeps_attr_start_and_key_same_calls(tmp_path):
    """R2-F1 — 속성 입구 + 상품: 속성 시작을 끄지 않고 상품 이름 길을 더한다 · 키 명세도 같은 함수."""
    from clinic_chat.evalset.key_calls import spec_from_item

    S2 = {"entity_table": "site", "entity_code": "ZZS-2", "entity_name": "가짜", "text": "가짜"}
    f = await run(tmp_path, make_hub(resolve=[cand(S2, start=0)]), jev_noul(kinds={"offer": 0.9}))
    assert f.attr_start and {(e.table, e.name) for e in f.entries} == {("site", "가짜"), ("offer_name", "가짜")}
    b = plan_calls(f.entries, f.kinds)
    got = [(c.request.get("facet"), c.request.get("node") or c.request.get("offer_name_contains")) for c in b]
    # 0.6 KD11 — 상품이 yes 로 선 범위라 no 이하인 기본 묶음(개요 · 어디에 쓰나)은 읽지 않고 `offered`
    assert got == [("offer", "site:ZZS-2"), ("offer", "가짜")]
    assert {o["code"] for o in f.offered if o.get("by") == "kd11"} == {"overview", "indication"}
    item = {"id": "N92", "range": "상품", "expected_kinds": f.kinds,
            "entries": ["site:ZZS-2", {"table": "offer_name", "code": "", "name": "가짜"}]}
    spec = spec_from_item(item, {"site:ZZS-2": "가짜"})
    assert sorted(str(c["request"]) for c in spec["calls"]) == sorted(str(c.request) for c in b)
