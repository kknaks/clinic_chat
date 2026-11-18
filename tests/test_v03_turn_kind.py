"""0.3 WP-3 — 턴 종류(SPEC-006 §2) — 0.4(SPEC-008 · WORK-017 §15-4): 가르기 Noul · 걷기 거둠 · 두 번째 걷기 · 든 후보 · 줄임 후보
시험 13 은 지웠다(켠 상태 기대가 없어짐 — X-8 ~ X-12 · X-25). 남은 것: 앞 대상(`follow_target`) · TT5 단계 · TT6 잇단 talk(0.4 꼴)."""

from __future__ import annotations

import re

from clinic_chat.consult import state as st
from clinic_chat.consult import turn_kind as tk
from tests.state_fixture import Run
from tests.v04_fixture import set_out


def test_s6_t2_talk_steps_understand_then_answer(tmp_path, fake_cli):
    """TT5 · UC10 — `talk` 턴의 단계 = `understand` → `answer`(0.4 꼴 — 가짜 이해 출력 `talk`)."""
    r = Run(tmp_path, fake_cli, chat_ask_max=0)
    try:
        set_out(fake_cli[1], [("concern", "ZZC-1")], kinds=["indication"])
        r.turn("가짜고민 알려줘")
        set_out(fake_cli[1], [], kind="talk")
        _, _, body = r.turn("응 맞아")
        keys = set()
        for block in body.split("\n\n"):
            if "event: step" in block:
                keys |= set(re.findall(r'data-step="(\w+)"', block))
        assert keys == {"understand", "answer"}
    finally:
        r.close()


def test_s6_t4_follow_target_order_and_withdrawn():
    obj = st.empty()
    assert tk.follow_target(obj, None) is None
    obj["concerns"] = [{"table": "concern", "code": "C1", "name": "c", "state": "said",
                        "history": [{"turn": 1, "state": "said"}]}]
    obj["focus"] = "C1"
    assert tk.follow_target(obj, None)["code"] == "C1"  # ③ focus
    obj["subjects"] = [{"table": "procedure", "code": "P1", "name": "p", "state": "said",
                        "history": [{"turn": 1, "state": "said"}]},
                       {"table": "product", "code": "R1", "name": "r", "state": "confirmed",
                        "history": [{"turn": 3, "state": "confirmed"}]},
                       {"table": "product", "code": "R2", "name": "w", "state": "withdrawn",
                        "history": [{"turn": 4, "state": "withdrawn"}]}]
    assert tk.follow_target(obj, None)["code"] == "R1"  # ② 가장 최근 축 subjects · withdrawn 빼고
    prev = {"targets": [{"table": "package", "code": "K1", "name": "k"}, {"table": "procedure", "code": "P1"}]}
    assert tk.follow_target(obj, prev) == {"table": "package", "code": "K1", "name": "k"}  # ① 객체 밖 표(W-3)


# ───── S6-T7 잇단 talk(VF11) ─────

def test_s6_t7_talk_max(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, chat_talk_max=1, chat_ask_max=0)
    try:
        set_out(fake_cli[1], [("concern", "ZZC-1")], kinds=["indication"])
        r.turn("가짜고민 알려줘")
        set_out(fake_cli[1], [], kind="talk")
        row2, _, _ = r.turn("응")
        row3, _, _ = r.turn("그렇군요")
        assert row2["consult"]["turn_kind"] == {"kind": "talk", "from": "understand"}
        assert row3["consult"]["turn_kind"] == {"kind": "new", "from": "talk_max"}
    finally:
        r.close()
