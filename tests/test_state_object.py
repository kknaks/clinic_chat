"""WP-1 — 상담 객체 · 갱신 · 장부(S5-T1 · T2 · T4 · T5 · T7 · T13 · T17 · T18) — 가짜 셋 · 켠 상태 명시."""

from __future__ import annotations

import pytest

from clinic_chat.consult import entry_list as el_mod
from clinic_chat.consult import plan
from clinic_chat.consult import site_filter as sf
from clinic_chat.consult import state as st
from clinic_chat.consult import verify as vf
from clinic_chat.consult.ledger import Ledger
from tests.state_fixture import Run, state_hub
from tests.v04_fixture import entry_rows, set_out

EL = el_mod.build(entry_rows(), "fakehash", aliases=True)


def acc(targets, *, kinds=(), kind="new", obj=None, withdraw=()):
    """0.4 — 관찰 재료 = 검증 뒤 이해 출력(VF12). targets = [(table, code) | (table, code, sure)]."""
    tg = [{"table": x[0], "code": x[1], "sure": x[2] if len(x) > 2 else True} for x in targets]
    return vf.verify({"targets": tg, "kind": kind, "sites": [], "withdraw": list(withdraw), "kinds": list(kinds)}, EL,
                     obj if obj is not None else st.empty(), first_turn=False, talk_streak=0, talk_max=3, prev_row=None)


def apply(obj, targets, turn, retract=(), *, kinds=()):
    last = obj["asked"][-1] if obj["asked"] else None
    a = acc(targets, kinds=kinds, obj=obj)
    obs = st.observe_accepted(a, last if last and last["turn"] == turn - 1 else None, obj)
    return st.update(obj, obs, [k for k in a.kinds if k in st.WISH_KINDS], turn, set(retract))


def test_s5_t1_slots_closed_lists():
    obj = st.empty()
    apply(obj, [("concern", "ZZC-1"), ("site", "ZZS-1"), ("product", "ZZR-1"), ("effect", "ZZE-1"), ("adverse", "ZZA-1"),
                ("condition", "ZZD-1"), ("offer", "ZZO-1"), ("anatomy", "ZZN-1")], 1, kinds=["recovery", "adverse"])
    assert set(obj) == {"concerns", "focus", "sites", "concern_sites", "subjects", "procedures", "wishes", "shown", "asked"}
    assert [x["code"] for x in obj["concerns"]] == ["ZZC-1"] and [x["code"] for x in obj["sites"]] == ["ZZS-1"]
    assert [x["code"] for x in obj["subjects"]] == ["ZZR-1"]
    tables = {x.get("table") for s in st.ITEM_SLOTS for x in obj[s]}
    assert not tables & {"adverse", "condition", "offer", "anatomy", "package"}
    for s in ("concerns", "sites", "subjects"):
        for x in obj[s]:
            assert x["code"] and x["name"] and x["state"] in st.STATES and x["source"]["how"] in st.HOWS
    kinds = [x for x in obj["wishes"] if "kind" in x]
    assert [x["kind"] for x in kinds] == ["recovery"]  # 바람 종류 = 이해 출력 kinds 가운데 recovery · outcome(OU11)
    assert set(st.WISH_KINDS) <= set(st.KIND_CODES_14) and all(set(x) == {"kind", "state", "source", "history"}
                                                                 for x in kinds)  # N-6
    assert [x["code"] for x in obj["wishes"] if x.get("table")] == []  # 0.4 LD8 — effect 표는 목록에 없다(검증이 버림)
    assert set(st.HOWS) == {"text", "option", "derived", "carried", "view"}  # 검수 N-5 · 0.3 B-2(누른 하위 고민 how = view)


def test_s5_t2_t17_certainty_named_not_in_object():
    obj = st.empty()
    apply(obj, [("concern", "ZZC-1"), ("site", "ZZS-1", False), ("site", "ZZS-404"), ("concern", "ZZC-5")], 1)
    assert [(x["code"], x["state"]) for x in obj["concerns"]] == [("ZZC-1", "said")]  # OU2 — sure false → guess
    assert [(x["code"], x["state"]) for x in obj["sites"]] == [("ZZS-1", "guess")]
    assert st.find(obj, "site", "ZZS-404") is None and st.find(obj, "concern", "ZZC-5") is None  # 목록에 없음 → 안 든다
    apply(obj, [("site", "ZZS-1"), ("concern", "ZZC-1")], 2)
    assert st.find(obj, "site", "ZZS-1")["state"] == "confirmed" and st.find(obj, "concern", "ZZC-1")["state"] == "confirmed"


def test_s5_t4_derived_procedures(tmp_path):
    obj = st.empty()
    apply(obj, [("product", "ZZR-1"), ("modality", "ZZM-1")], 1)
    hub = state_hub()

    async def go():
        r = plan.Reader(hub.client(page_limit=50), {}, [], [])
        await plan.run(obj, r)
        return r
    import asyncio
    r = asyncio.run(go())
    assert [(p["code"], p["via"]["subject"], p["via"]["scope_gid"]) for p in obj["procedures"]] == \
        [("ZZP-1", "product:ZZR-1", "GSC-90003")]  # 계열 대상은 시술을 정하지 않는다
    st.update(obj, [], [], 2, {"product:ZZR-1"})
    plan.derive(obj, r.memo)
    assert obj["procedures"] == []  # UP8 — 근거를 거두면 빠진다


def test_s5_t5_guess_is_not_an_axis():
    obj = st.empty()
    apply(obj, [("concern", "ZZC-1", False), ("site", "ZZS-1", False)], 1)
    assert obj["focus"] is None and st.axis_items(obj, "concerns") == [] and st.axis_items(obj, "sites") == []
    assert plan.qy1_calls(obj, ["가짜부위"]) == []


def test_s5_t7_two_concerns_focus_moves_and_returns():
    obj = st.empty()
    apply(obj, [("concern", "ZZC-1")], 1)
    apply(obj, [("concern", "ZZC-2")], 2)
    assert obj["focus"] == "ZZC-2" and [x["code"] for x in obj["concerns"]] == ["ZZC-1", "ZZC-2"]
    apply(obj, [("concern", "ZZC-1")], 3)
    assert obj["focus"] == "ZZC-1"
    apply(obj, [("concern", "ZZC-1"), ("concern", "ZZC-2")], 4)
    assert obj["focus"] == "ZZC-2"  # OU9 — 한 말에 둘이면 targets 순서로 마지막
    d = st.update(obj, [], [], 5, {"concern:ZZC-2"})
    assert obj["focus"] == "ZZC-1" and any(x["op"] == "focus" for x in d)  # RV7


def test_w2_same_name_site_pairs_with_concern():
    obj = st.empty()
    a = vf.verify({"targets": [{"table": "concern", "code": "ZZC-9", "sure": True},
                               {"table": "site", "code": "ZZS-9", "sure": True}], "kind": "new", "sites": ["ZZS-9"]},
                  EL, obj, first_turn=True, talk_streak=0, talk_max=3, prev_row=None)
    st.update(obj, st.observe_accepted(a, None, obj), [], 1, set())
    s = st.find(obj, "site", "ZZS-9")
    assert s["state"] == "said" and s["source"]["pair_concern"] == "ZZC-9"
    names = [EL.name("site", c) for c in sf.decide(a, obj, None, obj["focus"])["sites"]]
    assert [c.request for c in plan.qy1_calls(obj, names)] == [{"concern_code": "ZZC-9", "site": "가짜팔자"}]
    apply(obj, [("concern", "ZZC-1")], 2)
    assert sf.decide(a, obj, None, "ZZC-1")["sites"] == []  # QS3 — 짝 고민이 focus 가 아니면 거르개 아님
    assert obj["focus"] == "ZZC-1" and [c.request for c in plan.qy1_calls(obj, [])] == [{"concern_code": "ZZC-1"}]


@pytest.fixture
def run(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli)
    r.route = fake_cli[1]
    yield r
    r.close()


def test_s5_t13_ledger_rebuild_and_error_turn_not_counted(run, tmp_path):
    set_out(run.route, [("concern", "ZZC-1")], kinds=["indication"])
    row1, _, _ = run.turn("가짜고민 시술")
    assert run.rows("start")[0]["consult_state"] == "on"
    assert {"diff", "after", "ask", "blanks", "queries"} <= set(row1["consult"])
    set_out(run.route, [("product", "ZZR-1")])
    row2, _, _ = run.turn("가짜제품 은요")
    led = Ledger(tmp_path / "chat")
    conv = led.get(run.cid)
    assert conv.consult_state == "on" and conv.consult == row2["consult"]["after"]
    bad = {"conv_id": run.cid, "phase": "end", "turn": 99, "error": {"kind": "x"},
           "consult": {"diff": [{"op": "add"}], "after": {"concerns": ["bogus"]}}}
    Ledger._apply_end(conv, bad)
    assert conv.consult == row2["consult"]["after"]  # RV9 — 오류 턴의 객체는 안 선다


def test_s5_t18_terminal_turn_updates_but_no_list_then_catches_up(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli)
    set_out(fake_cli[1], [("concern", "ZZC-1")], kinds=["indication"])
    r.turn("가짜고민 시술")
    set_out(fake_cli[1], [], withdraw=["concern:ZZC-1"], oos=True)  # 대상 0 · 종류 0 · out → 「범위 밖」(QM3)
    row, kinds, _ = r.turn("아무 상관 없는 말")
    assert row["end_state"] == "범위 밖" and "list" not in kinds and "sites" not in kinds
    assert [(d["op"], d["code"]) for d in row["consult"]["diff"] if d["op"] == "withdraw"] == [("withdraw", "ZZC-1")]
    assert row["consult"]["queries"] == [] and row["consult"]["ask"] is None and row["consult"]["ask_why"] == "NQ7"
    assert row["consult"]["after"]["focus"] is None
    r.close()


def test_s5_t18_restart_catches_up_unread_query(tmp_path, fake_cli):
    """서버를 다시 띄운 뒤(대화 메모리 빔) — 객체에 있는데 아직 안 읽은 조회는 다음 턴에 허브에서 다시 읽는다(B-10)."""
    r = Run(tmp_path, fake_cli)
    set_out(fake_cli[1], [("concern", "ZZC-1")], kinds=["indication"])
    r.turn("가짜고민 시술")
    r.close()
    r2 = Run(tmp_path, fake_cli)
    set_out(fake_cli[1], [("procedure", "ZZP-1")], kinds=["overview"])
    r2.cid, r2.n = r.cid, 1
    row, kinds, _ = r2.turn("가짜시술 은 뭐예요")
    assert row["consult"]["after"]["focus"] == "ZZC-1"
    assert any(q["rule"] == "QY1" and q["skipped"] is None for q in row["consult"]["queries"])
    r2.close()


def test_off_state_has_no_consult(tmp_path, fake_cli):
    """0.4 CP1 — `CHAT_STATE=off` 를 주어도 새 대화는 켠 상태로 연다(끈 상태로 시작하는 새 대화는 없다)."""
    r = Run(tmp_path, fake_cli, state="off")
    set_out(fake_cli[1], [("concern", "ZZC-1")], kinds=["indication"])
    row, kinds, _ = r.turn("가짜고민 시술")
    assert "consult" in row and r.rows("start")[0]["consult_state"] == "on"
    r.close()
