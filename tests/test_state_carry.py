"""WP-2 — 걸린 선택지 · 거둠(S5-T3 · T6 · T20 · 검수 W-4 · 코디 W-8 · N-8) — 가짜 셋 · 켠 상태 명시."""

from __future__ import annotations

import asyncio

import pytest

from clinic_chat.consult import carry
from clinic_chat.walk.front import front
from tests.conftest import make_settings
from tests.fake_jev import FakeJev
from tests.state_fixture import Run, jev, state_hub


@pytest.fixture
def run(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev({"가짜제품": 0.9}, kinds={"overview": 0.9}))
    yield r
    r.close()


def _cands(fj, n_req: int) -> list[dict]:
    return fj.requests[n_req]["state"].get("candidates") or []


def test_s5_t3_options_carried_yes_only_no_entry_pick_one_turn(run):
    row1, _, _ = run.turn("가짜제품 받고 싶어요")
    assert row1["consult"]["ask"]["slot"] == "concern"
    assert [o["code"] for o in row1["consult"]["ask"]["options"]] == ["ZZC-1", "ZZC-2"]
    before = len(run.fj.requests)
    run.set_noul(jev({"가짜고민": 0.92, "가짜윗고민": 0.2, "가짜제품": 0.9}, kinds={"indication": 0.9}))
    row2, kinds, _ = run.turn("요즘 그게 좀 그래요")  # 글자 맞춤 0
    c = run.fj.requests[before]["state"]["candidates"]
    assert [x.get("option_from") for x in c if x["table"] == "concern"] == [1, 1]
    assert row2["trace"]["entry_pick"] is None  # 입구 고르기가 안 돈다
    after = row2["consult"]["after"]
    assert [(x["code"], x["source"]["how"], x["source"]["from_ask"]) for x in after["concerns"]] == [("ZZC-1", "option", 1)]
    assert {(x["code"], x["decision"]) for x in row2["consult"]["options_carried"]} == {("ZZC-1", "carried"),
                                                                                      ("ZZC-2", "dropped")}
    assert [x["code"] for x in row2["carried"]] == ["ZZR-1"] and all("origin" not in x for x in row2["carried"])  # N-8
    assert after["asked"][0]["answered"] is True and after["focus"] == "ZZC-1"
    n = len(run.fj.requests)
    run.turn("가짜시술 은요")
    assert not any(x.get("option_from") for x in _cands(run.fj, n))  # UP5 — 한 턴만 산다


def test_w4_option_answered_by_name_is_text_and_counts(run):
    run.turn("가짜제품 받고 싶어요")
    run.set_noul(jev({"가짜고민": 0.92, "가짜제품": 0.9}, kinds={"indication": 0.9}))
    row2, _, _ = run.turn("가짜고민 때문에요")
    it = next(x for x in row2["consult"]["after"]["concerns"] if x["code"] == "ZZC-1")
    assert it["source"]["how"] == "text" and it["source"]["from_ask"] == 1
    assert row2["consult"]["after"]["asked"][0]["answered"] is True


def test_w8_all_options_fail_then_entry_pick_runs(run):
    run.turn("가짜제품 받고 싶어요")
    run.set_noul(jev({"가짜고민": 0.1, "가짜윗고민": 0.1, "가짜제품": 0.1}, kinds={"indication": 0.9}))
    row2, _, _ = run.turn("글쎄요 잘 모르겠어요")
    assert row2["trace"]["entry_pick"] is not None  # CR6 뒷절 — 입구 고르기
    assert row2["trace"]["front"]["jev_requests"] >= 1 and row2["consult"]["jev_requests"]["entry_pick"] is not None
    assert row2["consult"]["after"]["asked"][0]["answered"] is False


def test_s5_t6_retract(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev(kinds={"indication": 0.9}))
    r.turn("가짜고민 시술")
    r.turn("가짜윗고민 도요")
    assert r.rows()[-1]["consult"]["after"]["focus"] == "ZZC-2"
    r.set_noul(jev({"가짜고민": 0.9}, kinds={"overview": 0.9}, retract={"concern:가짜윗고민": 0.95, "concern:가짜고민": 0.5,
                                                                    "product:가짜제품": 0.9}))
    row, _, _ = r.turn("가짜제품 이요")
    after = row["consult"]["after"]
    st = {x["code"]: x["state"] for x in after["concerns"]}
    assert st["ZZC-2"] == "withdrawn" and st["ZZC-1"] in ("said", "confirmed")  # 사이 값은 그대로
    assert after["focus"] == "ZZC-1"  # RV7 — 남은 가장 최근
    assert not any(x["code"] == "ZZR-1" for x in after["subjects"])  # RV3 — 이번 글의 이름이라도 거둠 yes 면 안 든다
    assert row["consult"]["retract_requests"] == 1 and row["consult"]["retract_ms"] is not None
    r.close()


def test_retract_failure_keeps_turn(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev(kinds={"indication": 0.9}))
    r.turn("가짜고민 시술")
    orig = r.fj.noul_fn

    def boom(name, instr, state):
        if name.startswith("retract_"):
            raise RuntimeError("fixture")
        return orig(name, instr, state)
    r.set_noul(boom)
    row, kinds, _ = r.turn("가짜고민 더요")
    assert row["error"] is None and "done" in kinds and row["consult"]["retract_error"]  # B-5
    r.close()


def test_with_options_merges_previous_target():
    out = carry.with_options([{"table": "concern", "code": "C1", "name": "a", "from_turn": 1}],
                             [{"slot": "concern", "about": "x", "turn": 1,
                               "options": [{"table": "concern", "code": "C1", "name": "a"},
                                           {"table": "concern", "code": "C2", "name": "b"}]}], 2)
    assert [(x["code"], x.get("origin")) for x in out] == [("C1", "option"), ("C2", "option")]
    assert carry.with_options([], [{"slot": "c", "about": "x", "turn": 1, "options": [{"table": "t", "code": "c"}]}], 3) == []


def test_s5_t20_front_without_origin_is_unchanged(tmp_path):
    s = make_settings(tmp_path)
    hub = state_hub()
    fj = FakeJev(noul_fn=jev(kinds={"indication": 0.9}))
    carried = [{"table": "concern", "code": "ZZC-1", "name": "가짜고민", "from_turn": 1}]
    f = asyncio.run(front("가짜시술 은요", hub.client(page_limit=50), fj.jev(s), s, carry=carried))
    assert all("origin" not in x for x in f.carried)
    tr = [x for x in f.trace_front["targets"] if x["id"] == "concern:ZZC-1"][0]
    assert tr["carried_from"] == 1 and "option_from" not in tr
    st = fj.requests[0]["state"]["candidates"]
    assert st[-1]["carried_from"] == 1 and "option_from" not in st[-1]
    fj2 = FakeJev(noul_fn=jev(kinds={"indication": 0.9}))
    instr = {"target": "T {name}", "target_carried": "C {name}", "target_option": "O {name}", "kind": "K {name}",
             "gap": "G"}
    asyncio.run(front("가짜시술 은요", hub.client(page_limit=50), fj2.jev(s), s, instr=instr,
                      carry=[{**carried[0], "origin": "option"}]))
    q = fj2.requests[0]["questions"]
    assert q["target_1"]["instructions"].startswith("O ") and q["target_0"]["instructions"].startswith("T ")
