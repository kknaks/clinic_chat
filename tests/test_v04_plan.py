"""S8-T8(조회 몫) · S8-T13 · S8-T16 — 객체 기준 조회(SPEC-008 QS1 ~ QS6 · OU5 · WORK-017 WP-5). 가짜 CLI · 가짜 허브."""

from __future__ import annotations

import pytest

from clinic_chat.consult import entry_list as el_mod
from clinic_chat.consult import site_index as si_mod
from tests.state_fixture import Run, state_hub
from tests.v04_fixture import set_out

S3 = {"ZZC-3": [{"concern_code": "ZZC-3", "site_code": "ZZS-1", "site_name": "가짜부위", "csi_id": "CSI-9001",
                 "match": "직접"}],
      "ZZC-4": [{"concern_code": "ZZC-4", "site_code": "ZZS-2", "site_name": "가짜부위둘", "csi_id": "CSI-9002",
                 "match": "직접"}]}


@pytest.fixture(autouse=True)
def _clear():
    el_mod.clear_cache()
    si_mod.clear_cache()
    yield
    el_mod.clear_cache()
    si_mod.clear_cache()


def _cc(hub, since=0):
    return [p for t, p in hub.requests[since:] if t == "concern_candidates"]


@pytest.fixture
def run(tmp_path, fake_cli, monkeypatch):
    from tests import state_fixture as sfx
    monkeypatch.setattr(sfx, "EXTRA_CONCEPTS", [*sfx.EXTRA_CONCEPTS, ("site", "ZZS-2", "가짜부위둘")])
    hub = state_hub(sites=S3)
    r = Run(tmp_path, fake_cli, hub=hub, chat_ask_max=0)
    r.route = fake_cli[1]
    yield r
    r.close()


def test_s8_t8_second_turn_filter_is_this_turn_site_only(run):
    set_out(run.route, [("concern", "ZZC-1"), ("site", "ZZS-1")], sites=["ZZS-1"], kinds=["indication"])
    run.turn("가짜고민 가짜부위")
    n = len(run.hub.requests)
    set_out(run.route, [("concern", "ZZC-1"), ("site", "ZZS-2")], sites=["ZZS-2"], kinds=["indication"])
    row, _, _ = run.turn("가짜고민 가짜부위둘")
    assert row["consult"]["site_filter"]["sites"] == ["ZZS-2"]
    q = [x for x in row["consult"]["queries"] if x["rule"] in ("QY1", "QY5")]
    assert [x["request"].get("site") for x in q] == ["가짜부위둘"]  # 앞 턴 부위(가짜부위) 없음 · 조회에 건 값 = 장부 값
    assert all(p.get("site") in (None, "가짜부위둘") for p in _cc(run.hub, n))


def test_s8_t8_no_site_same_focus_keeps_list_and_new_focus_queries_plain(run):
    set_out(run.route, [("concern", "ZZC-1"), ("site", "ZZS-1")], sites=["ZZS-1"], kinds=["indication"])
    run.turn("가짜고민 가짜부위")
    n = len(run.hub.requests)
    set_out(run.route, [("concern", "ZZC-1")], kinds=["indication"])
    row, kinds, _ = run.turn("가짜고민 다시요")
    assert row["consult"]["site_filter"]["sites"] == [] and "list" not in kinds  # QS4 — 앞 리스트 그대로
    assert all(p.get("site") != "" for p in _cc(run.hub, n))
    assert not [x for x in row["consult"]["queries"] if x["rule"] in ("QY1", "QY5") and x["skipped"] is None
                and x["request"].get("site") is None]  # 거르개 없는 조회로 덮어쓰지 않는다
    set_out(run.route, [("concern", "ZZC-2")], kinds=["indication"])
    row, kinds, _ = run.turn("가짜윗고민")
    q = [x for x in row["consult"]["queries"] if x["rule"] == "QY1"]
    assert [x["request"] for x in q][:1] == [{"concern_code": "ZZC-2"}]  # focus 바뀜 + 부위 없음 → 거르개 없는 하나


def test_s8_t13_qy7_bound_concerns_first_and_index_once(run):
    set_out(run.route, [("site", "ZZS-1")], sites=["ZZS-1"])
    row, _, _ = run.turn("가짜부위요")
    blanks = [b for b in row["consult"]["blanks"] if b["bk"] == "BK2s"]
    assert blanks and blanks[0]["options_n"] == 2  # 묶인 ZZC-3 + facets 의 ZZC-1
    idx = row["consult"]["site_index"]
    assert idx["built"] is True and idx["concerns"] == 5 and idx["calls"] == 5 and idx["ms"] is not None
    n = len(run.hub.requests)
    set_out(run.route, [("site", "ZZS-2")], sites=["ZZS-2"])
    row2, _, _ = run.turn("가짜부위둘이요")
    assert "site_index" not in row2["consult"]  # 지은 턴에만
    assert len([p for p in _cc(run.hub, n) if p.get("limit") == "1"]) == 0  # 색인은 gold_hash 마다 한 번
    b2 = [b for b in row2["consult"]["blanks"] if b["bk"] == "BK2s" and b["about"] == "site:ZZS-2"]
    assert b2 and b2[0]["options_n"] == 1  # facets 0 행이어도 묶인 고민(ZZC-4)이 나온다


def test_s8_t13_bound_first_order(tmp_path, fake_cli, monkeypatch):
    from tests import state_fixture as sfx
    monkeypatch.setattr(sfx, "EXTRA_CONCEPTS", [*sfx.EXTRA_CONCEPTS, ("site", "ZZS-2", "가짜부위둘")])
    hub = state_hub(sites=S3)
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        set_out(fake_cli[1], [("site", "ZZS-1")], sites=["ZZS-1"])
        row, _, _ = r.turn("가짜부위요")
        ask = row["consult"]["ask"]
        assert ask["slot"] == "concern" and ask["about"] == "site:ZZS-1"
        assert [o["code"] for o in ask["options"]] == ["ZZC-3", "ZZC-1"]  # 묶인 고민 먼저 · 같은 고민 한 번
    finally:
        r.close()


def test_s8_t16_new_repeats_target_no_site_no_requery(run):
    set_out(run.route, [("concern", "ZZC-1")], kinds=["indication"])
    run.turn("가짜고민 시술")
    n = len(run.hub.requests)
    set_out(run.route, [("concern", "ZZC-1")])
    row, kinds, _ = run.turn("가짜고민 그거요")
    obj_q = [x for x in row["consult"]["queries"] if x["skipped"] is None]
    assert obj_q == [] and "list" not in kinds  # 객체 기준 조회의 허브 호출 0(메모) · list 이벤트 없음
    assert not [t for t, p in run.hub.requests[n:] if t in ("scope", "pair") or
                (t == "facets" and p.get("facet") == "indication")]


def test_l4_index_cached_even_with_failed_concern():
    """검수 L-4 — 고민 하나의 조회가 깨져도 그 고민만 빼고 색인을 캐시한다(QY7 턴마다 다시 짓지 않는다)."""
    import asyncio

    from tests.v04_fixture import entry_rows

    hub = state_hub(sites=S3)
    orig = hub.routes["concern_candidates"]
    hub.routes["concern_candidates"] = lambda p: (500, {"detail": "x"}) if p.get("concern_code") == "ZZC-1" else orig(p)
    el = el_mod.build(entry_rows(), "fakehash", aliases=True)
    c = hub.client(page_limit=50)
    codes, rec = asyncio.run(si_mod.concerns_of_site(c, el, "ZZS-1"))
    assert codes == ["ZZC-3"] and rec["errors"] == 1
    n = len(hub.requests)
    codes2, rec2 = asyncio.run(si_mod.concerns_of_site(c, el, "ZZS-2"))
    assert rec2 is None and len(hub.requests) == n and codes2 == ["ZZC-4"]
