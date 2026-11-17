"""WP-1 — 상담 객체 · 갱신 · 장부(S5-T1 · T2 · T4 · T5 · T7 · T13 · T17 · T18) — 가짜 셋 · 켠 상태 명시."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from clinic_chat.consult import plan
from clinic_chat.consult import state as st
from clinic_chat.consult.ledger import Ledger
from tests.state_fixture import Run, jev, state_hub


def fake_walk(targets: list[dict], *, kinds: list[dict] | None = None, end_state: str = "근거 충족"):
    """걷기 trace 꼴만(UP1) — targets = [{id, name, decision, noul, start?, span?, option_from?, carried_from?}]."""
    rc = [{"id": t["id"], "span": t.get("span", t["name"]), "start": t.get("start", 0), "covered_by": t.get("covered_by")}
          for t in targets]
    tr = [{k: v for k, v in t.items() if k not in ("start", "span")} for t in targets]
    front = SimpleNamespace(trace_front={"targets": tr, "kinds": kinds or []}, trace_entry={"resolve_candidates": rc},
                            entries=[], kinds=[], carried=[])
    return SimpleNamespace(front=front, end_state=end_state, reads=[])


def t(tid, name, dec, noul=0.9, **kw):
    return {"id": tid, "name": name, "decision": dec, "noul": noul, "covered_by": None, **kw}


def apply(obj, w, turn, retract=()):
    last = obj["asked"][-1] if obj["asked"] else None
    obs, kinds = st.observe(w, last if last and last["turn"] == turn - 1 else None)
    return st.update(obj, obs, kinds, turn, set(retract))


def test_s5_t1_slots_closed_lists():
    obj = st.empty()
    w = fake_walk([t("concern:ZZC-1", "가짜고민", "target"), t("site:ZZS-1", "가짜부위", "target", start=5),
                   t("product:ZZR-1", "가짜제품", "target"), t("effect:ZZE-1", "가짜효과", "target"),
                   t("adverse:ZZA-1", "가짜사건", "target"), t("condition:ZZD-1", "가짜조건", "target"),
                   t("offer:ZZO-1", "가짜상품", "target"), t("anatomy:ZZN-1", "가짜해부", "target")],
                  kinds=[{"code": "recovery", "decision": "read", "by": "yes"},
                         {"code": "adverse", "decision": "read", "by": "yes"},
                         {"code": "outcome", "decision": "read", "by": "default"}])
    apply(obj, w, 1)
    assert set(obj) == {"concerns", "focus", "sites", "concern_sites", "subjects", "procedures", "wishes", "shown", "asked"}
    assert [x["code"] for x in obj["concerns"]] == ["ZZC-1"] and [x["code"] for x in obj["sites"]] == ["ZZS-1"]
    assert [x["code"] for x in obj["subjects"]] == ["ZZR-1"]
    tables = {x.get("table") for s in st.ITEM_SLOTS for x in obj[s]}
    assert not tables & {"adverse", "condition", "offer", "anatomy", "package"}
    for s in ("concerns", "sites", "subjects"):
        for x in obj[s]:
            assert x["code"] and x["name"] and x["state"] in st.STATES and x["source"]["how"] in st.HOWS
    kinds = [x for x in obj["wishes"] if "kind" in x]
    assert [x["kind"] for x in kinds] == ["recovery"]  # 바람 종류 = recovery · outcome 가운데 yes 로 읽은 것만(B-3)
    assert set(st.WISH_KINDS) <= set(st.KIND_CODES_14) and all(set(x) == {"kind", "state", "source", "history"}
                                                                 for x in kinds)  # N-6
    assert [x["code"] for x in obj["wishes"] if x.get("table")] == ["ZZE-1"]
    assert set(st.HOWS) == {"text", "option", "derived", "carried", "view"}  # 검수 N-5 · 0.3 B-2(누른 하위 고민 how = view)


def test_s5_t2_t17_certainty_named_not_in_object():
    obj = st.empty()
    w = fake_walk([t("concern:ZZC-1", "가짜고민", "target"),
                   t("site:ZZS-1", "가짜부위", "uncertain_kept", 0.5, start=4),
                   t("site:ZZS-2", "볼", "named", 0.1, start=8),  # W-1 — no 문턱 아래 우연 일치 → trace 에만
                   t("concern:ZZC-5", "가짜", "dropped", 0.5, covered_by="가짜고민")])
    apply(obj, w, 1)
    assert [(x["code"], x["state"]) for x in obj["concerns"]] == [("ZZC-1", "said")]
    assert [(x["code"], x["state"]) for x in obj["sites"]] == [("ZZS-1", "guess")]
    assert st.find(obj, "site", "ZZS-2") is None and st.find(obj, "concern", "ZZC-5") is None
    assert "site:ZZS-2" not in {i["id"] for i in st.retract_items(obj, [])}  # named 는 거둠 항목에도 없다
    apply(obj, fake_walk([t("site:ZZS-1", "가짜부위", "target"), t("concern:ZZC-1", "가짜고민", "target")]), 2)
    assert st.find(obj, "site", "ZZS-1")["state"] == "confirmed" and st.find(obj, "concern", "ZZC-1")["state"] == "confirmed"


def test_s5_t4_derived_procedures(tmp_path):
    obj = st.empty()
    apply(obj, fake_walk([t("product:ZZR-1", "가짜제품", "target"), t("modality:ZZM-1", "가짜계열", "target", start=5)]), 1)
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
    apply(obj, fake_walk([t("concern:ZZC-1", "가짜고민", "uncertain_kept", 0.5),
                          t("site:ZZS-1", "가짜부위", "uncertain_kept", 0.5, start=5)]), 1)
    assert obj["focus"] is None and st.axis_items(obj, "concerns") == [] and st.filter_sites(obj) == []
    assert plan.qy1_calls(obj) == []


def test_s5_t7_two_concerns_focus_moves_and_returns():
    obj = st.empty()
    apply(obj, fake_walk([t("concern:ZZC-1", "가짜고민", "target")]), 1)
    apply(obj, fake_walk([t("concern:ZZC-2", "가짜윗고민", "target")]), 2)
    assert obj["focus"] == "ZZC-2" and [x["code"] for x in obj["concerns"]] == ["ZZC-1", "ZZC-2"]
    apply(obj, fake_walk([t("concern:ZZC-1", "가짜고민", "target")]), 3)
    assert obj["focus"] == "ZZC-1"
    apply(obj, fake_walk([t("concern:ZZC-1", "가짜고민", "target", start=0),
                          t("concern:ZZC-2", "가짜윗고민", "target", start=9)]), 4)
    assert obj["focus"] == "ZZC-2"  # RV6 — 한 글에 둘이면 뒤에 나온 것
    d = st.update(obj, [], [], 5, {"concern:ZZC-2"})
    assert obj["focus"] == "ZZC-1" and any(x["op"] == "focus" for x in d)  # RV7


def test_w2_same_name_site_pairs_with_concern():
    obj = st.empty()
    apply(obj, fake_walk([t("concern:ZZC-9", "가짜팔자", "target", start=0), t("site:ZZS-9", "가짜팔자", "target", start=0)]), 1)
    s = st.find(obj, "site", "ZZS-9")
    assert s["state"] == "said" and s["source"]["pair_concern"] == "ZZC-9"
    assert [c.request for c in plan.qy1_calls(obj)] == [{"concern_code": "ZZC-9", "site": "가짜팔자"}]
    apply(obj, fake_walk([t("concern:ZZC-1", "가짜고민", "target")]), 2)
    assert obj["focus"] == "ZZC-1" and [c.request for c in plan.qy1_calls(obj)] == [{"concern_code": "ZZC-1"}]


@pytest.fixture
def run(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev(kinds={"indication": 0.9}))
    yield r
    r.close()


def test_s5_t13_ledger_rebuild_and_error_turn_not_counted(run, tmp_path):
    row1, _, _ = run.turn("가짜고민 시술")
    assert run.rows("start")[0]["consult_state"] == "on"
    assert {"diff", "after", "ask", "blanks", "queries"} <= set(row1["consult"])
    row2, _, _ = run.turn("가짜제품 은요")
    led = Ledger(tmp_path / "chat")
    conv = led.get(run.cid)
    assert conv.consult_state == "on" and conv.consult == row2["consult"]["after"]
    bad = {"conv_id": run.cid, "phase": "end", "turn": 99, "error": {"kind": "x"},
           "consult": {"diff": [{"op": "add"}], "after": {"concerns": ["bogus"]}}}
    Ledger._apply_end(conv, bad)
    assert conv.consult == row2["consult"]["after"]  # RV9 — 오류 턴의 객체는 안 선다


def test_s5_t18_terminal_turn_updates_but_no_list_then_catches_up(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev(kinds={"indication": 0.9}))
    r.turn("가짜고민 시술")
    r.set_noul(jev({"가짜고민": 0.1}, kinds={}, gap=0.95, retract={"가짜고민": 0.9}))
    row, kinds, _ = r.turn("아무 상관 없는 말")
    assert row["end_state"] == "범위 밖" and "list" not in kinds and "sites" not in kinds
    assert [(d["op"], d["code"]) for d in row["consult"]["diff"] if d["op"] == "withdraw"] == [("withdraw", "ZZC-1")]
    assert row["consult"]["queries"] == [] and row["consult"]["ask"] is None and row["consult"]["ask_why"] == "NQ7"
    assert row["consult"]["after"]["focus"] is None
    r.close()


def test_s5_t18_restart_catches_up_unread_query(tmp_path, fake_cli):
    """서버를 다시 띄운 뒤(대화 메모리 빔) — 객체에 있는데 아직 안 읽은 조회는 다음 턴에 허브에서 다시 읽는다(B-10)."""
    r = Run(tmp_path, fake_cli, noul=jev(kinds={"indication": 0.9}))
    r.turn("가짜고민 시술")
    r.close()
    r2 = Run(tmp_path, fake_cli, noul=jev({"가짜고민": 0.1}, kinds={"overview": 0.9}))
    r2.cid, r2.n = r.cid, 1
    row, kinds, _ = r2.turn("가짜시술 은 뭐예요")
    assert row["consult"]["after"]["focus"] == "ZZC-1"
    assert any(q["rule"] == "QY1" and q["skipped"] is None for q in row["consult"]["queries"])
    r2.close()


def test_off_state_has_no_consult(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, state="off", noul=jev(kinds={"indication": 0.9}))
    row, kinds, _ = r.turn("가짜고민 시술")
    assert "consult" not in row and r.rows("start")[0]["consult_state"] == "off"
    assert "interest" not in json.dumps(row["list"])
    r.close()
