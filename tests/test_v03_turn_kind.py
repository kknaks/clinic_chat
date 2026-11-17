"""0.3 WP-3 — 턴 종류 · 걷기 순서(SPEC-006 §2 · S6-T1 ~ T5 · T7 · T25 · 코디 W-3 · W-6 · W-7 · 검수 W-5 · B-3)."""

from __future__ import annotations

import json

import httpx2
import pytest

from clinic_chat.consult import carry as carry_mod
from clinic_chat.consult import state as st
from clinic_chat.consult import turn_kind as tk
from clinic_chat.walk.front import ABBR
from tests.state_fixture import Run, jev, resolve_fn, state_hub

TK_NAMES = ("tk_new", "tk_talk", "tk_follow")


def jv(base=None, *, new=0.1, talk=0.1, follow=0.1, fail=False):
    base = base or jev(kinds={"indication": 0.9})
    vals = {"tk_new": new, "tk_talk": talk, "tk_follow": follow}

    def f(name, instr, state):
        if name in vals:
            if fail:
                raise httpx2.ReadTimeout("fixture")
            return vals[name]
        return base(name, instr, state)
    return f


def tk_reqs(fj, since: int = 0) -> list[dict]:
    return [r for r in fj.requests[since:] if "tk_new" in r["questions"]]


@pytest.fixture
def run(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jv())
    yield r
    r.close()


# ───── S6-T1 판정 ─────

def test_s6_t1_judge_order_and_threshold():
    assert tk.judge({"new": 0.9, "talk": 0.9, "follow": 0.9}, 0.7) == ("new", "TK5-1")
    assert tk.judge({"new": 0.5, "talk": 0.8, "follow": 0.9}, 0.7) == ("talk", "TK5-2")
    assert tk.judge({"new": 0.5, "talk": 0.6, "follow": 0.7}, 0.7) == ("follow", "TK5-3")
    assert tk.judge({"new": 0.5, "talk": 0.6, "follow": 0.6}, 0.7) == ("new", "TK5-4")


def test_s6_t1_first_turn_no_request_then_one_request(run):
    row1, _, _ = run.turn("가짜고민 알려줘")
    assert tk_reqs(run.fj) == [] and row1["consult"]["turn_kind"]["kind"] == "new"
    assert row1["consult"]["turn_kind"]["rule"] == "TK2"
    n = len(run.fj.requests)
    row2, _, _ = run.turn("가짜시술 개요")
    reqs = tk_reqs(run.fj, n)
    assert len(reqs) == 1 and set(TK_NAMES) <= set(reqs[0]["questions"])
    s = reqs[0]["state"]  # TK4 — 이름만(코드 0)
    assert s["prev"]["question"] == "가짜고민 알려줘" and s["prev"]["targets"] == ["가짜고민"]
    assert "ZZC-" not in json.dumps(s["prev"], ensure_ascii=False) + json.dumps(s["consult"], ensure_ascii=False)
    tkr = row2["consult"]["turn_kind"]
    assert tkr["kind"] == "new" and tkr["rule"] == "TK5-4" and tkr["nouls"] == {"new": 0.1, "talk": 0.1, "follow": 0.1}
    assert tkr["requests"] == 1 and tkr["error"] is None


def test_s6_t1_request_error_is_new_and_recorded(run):
    run.turn("가짜고민 알려줘")
    run.set_noul(jv(fail=True))
    row2, kinds, _ = run.turn("가짜시술 개요")
    tkr = row2["consult"]["turn_kind"]
    assert tkr["kind"] == "new" and tkr["rule"] == "TK6" and tkr["error"] and row2["error"] is None
    assert "done" in kinds and row2["targets"]


def test_s6_t1_extra_on_first_chunk_only(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jv(), jev_batch_size=1)
    try:
        r.turn("가짜고민 이랑 가짜시술")
        n = len(r.fj.requests)
        r.turn("가짜시술 개요")
        retr = [x for x in r.fj.requests[n:] if any(k.startswith("retract_") for k in x["questions"])]
        assert len(retr) >= 2 and len([x for x in retr if "tk_new" in x["questions"]]) == 1  # 검수 N-3
    finally:
        r.close()


# ───── S6-T2 talk 턴(W-6 · 검수 W-5 의 두 꼴) ─────

def test_s6_t2_talk_without_prev_ask_cancels_walk(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jv(), chat_ask_max=0)  # 앞 턴 `ask` 0
    try:
        row1, _, _ = r.turn("가짜고민 알려줘")
        assert row1["consult"]["ask"] is None
        r.set_noul(jv(talk=0.9))
        row2, kinds, _ = r.turn("왜 그런 거예요")
        c = row2["consult"]
        assert c["turn_kind"]["kind"] == "talk" and row2["targets"] == [] and row2["kinds"] == []
        assert row2["end_state"] is None and row2["material"]["new_lines"] == []
        assert c["queries"] == [] and "list" not in kinds and "sites" not in kinds  # 객체 조회 0(QY) · TT2
        w = c["turn_kind"]["walks"]
        assert len(w) == 1 and w[0]["used"] is False  # 거뒀거나(cancelled) 이미 끝난 걷기의 결과를 버렸다
        assert row2["trace"].get("cancelled") is True or (row2["trace"]["front"] and not w[0]["cancelled"])
        msg = row2["llm_user_message"]
        j = json.loads(msg.split("\n")[1])
        assert j["turn_kind"] == "talk" and j["seen_lines"] == [] and "end_state" not in j
        prev = json.loads(msg.split("### 앞 턴\n", 1)[1].split("\n")[0])
        assert set(prev) == {"closed_by", "empty", "end_state"}
        assert "## 자료\n(없음)" in msg
    finally:
        r.close()


def test_s6_t2_talk_steps_understand_then_answer(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jv(), chat_ask_max=0)
    try:
        r.turn("가짜고민 알려줘")
        r.set_noul(jv(talk=0.9))
        _, _, body = r.turn("응 맞아")
        keys = set()
        for block in body.split("\n\n"):
            if "event: step" in block:
                keys |= set(__import__("re").findall(r'data-step="(\w+)"', block))
        assert keys == {"understand", "answer"}  # TT5 · B-11
    finally:
        r.close()


def test_s6_t2_talk_with_prev_ask_waits_walk_and_discards(run):
    row1, _, _ = run.turn("가짜제품 받고 싶어요")  # BK2 ask
    assert row1["consult"]["ask"] is not None
    run.set_noul(jv(jev({"가짜고민": 0.1, "가짜윗고민": 0.1, "가짜제품": 0.9}, kinds={"indication": 0.9}), talk=0.9))
    row2, kinds, _ = run.turn("그게 무슨 말이에요")
    c = row2["consult"]
    assert c["turn_kind"]["kind"] == "talk" and row2["targets"] == [] and row2["material"]["new_lines"] == []
    w = c["turn_kind"]["walks"]
    assert len(w) == 1 and w[0]["used"] is False and w[0]["cancelled"] is False  # B-3 — 끝까지 돌고 재료만 버림
    assert row2["trace"]["front"] is not None and "cancelled" not in row2["trace"]
    assert c["queries"] == [] and "list" not in kinds  # 객체 변화 0 → 객체 조회 0


# ───── S6-T3 talk 턴의 객체 ─────

def test_s6_t3_talk_option_yes_enters_and_queries(run):
    run.turn("가짜제품 받고 싶어요")
    run.set_noul(jv(jev({"가짜고민": 0.92, "가짜윗고민": 0.2, "가짜제품": 0.9}, kinds={"indication": 0.9}), talk=0.9))
    row2, kinds, _ = run.turn("응 그거요")
    c = row2["consult"]
    assert c["turn_kind"]["kind"] == "talk"
    adds = [(x["slot"], x["code"], x["why"]) for x in c["diff"] if x["op"] == "add"]
    assert adds == [("concerns", "ZZC-1", "option")]  # 글에서 새 낱말 0
    assert any(q["rule"] == "QY1" for q in c["queries"]) and "list" in kinds  # TT2
    assert c["after"]["focus"] == "ZZC-1"


def test_s6_t3_talk_retract_yes_withdraws(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jv(), chat_ask_max=0)
    try:
        r.turn("가짜시술 개요")
        r.set_noul(jv(jev(retract={"가짜시술": 0.9}, kinds={"overview": 0.9}), talk=0.9))
        row2, _, _ = r.turn("아 그건 됐어요")
        c = row2["consult"]
        assert c["turn_kind"]["kind"] == "talk"
        assert ("withdraw", "ZZP-1") in {(x["op"], x["code"]) for x in c["diff"]}
    finally:
        r.close()


# ───── S6-T4 follow ─────

def test_s6_t4_follow_no_entry_reruns_with_prev_subject(run):
    run.set_noul(jv(jev(kinds={"overview": 0.9})))
    run.turn("가짜시술 개요")
    run.set_noul(jv(jev({"가짜시술": 0.1}, kinds={"overview": 0.9}), follow=0.9))
    row2, _, _ = run.turn("그건 얼마나 가요")
    c = row2["consult"]
    assert c["turn_kind"]["kind"] == "follow" and c["follow_target"] == {"table": "procedure", "code": "ZZP-1"}
    assert [w["entry_pick"] for w in c["turn_kind"]["walks"]] == [False, False]
    assert [w["used"] for w in c["turn_kind"]["walks"]] == [False, True]
    assert [(x["code"], x["by"]) for x in row2["targets"]] == [("ZZP-1", "carried")]


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


def test_tw7_carry_alive_in_object():
    obj = st.empty()
    obj["subjects"] = [{"table": "procedure", "code": "P1", "state": "said"},
                       {"table": "procedure", "code": "P2", "state": "withdrawn"}]
    carried = [{"table": "procedure", "code": "P1"}, {"table": "procedure", "code": "P2"},
               {"table": "product", "code": "R9"}, {"table": "package", "code": "K1"}]
    assert carry_mod.alive_in_object(carried, obj, st.SLOT_OF) == [{"table": "procedure", "code": "P1"},
                                                                   {"table": "package", "code": "K1"}]


# ───── S6-T5 new ─────

def test_s6_t5_new_with_match_one_walk_without_match_entry_pick(run):
    run.turn("가짜고민 알려줘")
    row2, _, _ = run.turn("가짜시술 개요")
    assert [w["entry_pick"] for w in row2["consult"]["turn_kind"]["walks"]] == [False]
    run.set_noul(jv(jev({"가짜시술": 0.1, "가짜고민": 0.1}, kinds={"indication": 0.9})))
    row3, _, _ = run.turn("요즘 피부가 푸석해요")  # 글자 맞춤 0 · 든 후보 yes 0 → 입구 고르기로 한 번 더
    run_walks = row3["consult"]["turn_kind"]["walks"]
    assert [w["entry_pick"] for w in run_walks] == [False, True] and run_walks[0]["used"] is False


def test_s6_t5_turn_kind_off_is_0_2_call_order(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jv(talk=0.9), chat_turn_kind="off")
    try:
        r.turn("가짜고민 알려줘")
        row2, _, _ = r.turn("가짜시술 개요")
        assert tk_reqs(r.fj) == [] and row2["consult"]["turn_kind"]["rule"] == "off"
        assert row2["consult"]["turn_kind"]["kind"] == "new" and len(row2["consult"]["turn_kind"]["walks"]) == 1
    finally:
        r.close()


# ───── S6-T7 잇단 talk ─────

def test_s6_t7_talk_max(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jv(talk=0.9), chat_talk_max=1, chat_ask_max=0)
    try:
        r.turn("가짜고민 알려줘")
        row2, _, _ = r.turn("응")
        n = len(r.fj.requests)
        row3, _, _ = r.turn("그렇군요")
        assert row2["consult"]["turn_kind"]["kind"] == "talk"
        assert row3["consult"]["turn_kind"] == {**row3["consult"]["turn_kind"], "kind": "new", "rule": "TT6"}
        assert tk_reqs(r.fj, n) == []
    finally:
        r.close()


# ───── S6-T25 줄임 후보 ─────

def _abbr_hub():
    hub = state_hub()

    def resolve(p):
        out = resolve_fn(p)
        t = p.get("text") or ""
        if "가짜줄임" in t:
            out["candidates"].append({"gold_table": "g_entry", "gid": "GEN-ZZC-1b", "entity_table": "concern",
                                      "entity_code": "ZZC-1", "entity_name": "가짜고민", "text": "가짜고민",
                                      "norm": "가짜고민", "matched": {"span": "가짜줄임", "start": t.index("가짜줄임"),
                                                                     "covered_by": None, "how": ABBR,
                                                                     "full": "가짜고민"}})
            out["total"] = len(out["candidates"])
        return out
    hub.routes["resolve"] = resolve
    return hub


@pytest.mark.parametrize("v,inside", [(0.5, False), (0.1, False), (0.9, True)])
def test_s6_t25_abbr_candidate_yes_only(tmp_path, fake_cli, v, inside):
    r = Run(tmp_path, fake_cli, hub=_abbr_hub(), noul=jv(jev({"가짜고민": v}, kinds={"indication": 0.9})))
    try:
        row, _, _ = r.turn("가짜줄임 때문에요")
        dec = {t["id"]: t["decision"] for t in row["trace"]["front"]["targets"]}
        assert dec["concern:ZZC-1"] == ("target" if inside else "dropped")
        assert ("ZZC-1" in {x["code"] for x in row["consult"]["after"]["concerns"]}) is inside
    finally:
        r.close()
