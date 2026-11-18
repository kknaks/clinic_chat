"""WP-2 — 걸린 선택지 · 거둠(S5-T3 · T6 · T20 · 검수 W-4 · 코디 W-8 · N-8) — 가짜 셋 · 켠 상태 명시."""

from __future__ import annotations

import asyncio

from clinic_chat.consult import carry
from clinic_chat.walk.front import front
from tests.conftest import make_settings
from tests.fake_jev import FakeJev
from tests.state_fixture import jev, state_hub

# 0.4(WORK-017 §15-4): 든 후보 · 입구 고르기 · 거둠 요청 시험 다섯(S5-T3 · W-4 · W-8 · S5-T6 · 거둠 실패)은 지웠다 —
# 선택지 답 · 거둠은 test_v04_object.py 가 0.4 꼴로 본다(X-9 · X-23). `carry.with_options` · `walk.front` 는 남는다.


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
