"""S8-T6 · S8-T7 · S8-T8(객체 · 거르개 몫) — 객체 갱신 · 부위 잇기 · 부위 거르개(SPEC-008 OU1 ~ OU12 · VF8 · OU7 · QS3 ·
WORK-017 WP-4). 외부 호출 0."""

from __future__ import annotations

from pathlib import Path

from clinic_chat.consult import entry_list as el_mod
from clinic_chat.consult import prompt as prompt_mod
from clinic_chat.consult import site_filter as sf
from clinic_chat.consult import state as cst
from clinic_chat.consult import verify as vf
from tests.v04_fixture import entry_rows

EL = el_mod.build(entry_rows(), "fakehash", aliases=True)
PICK = prompt_mod.load(Path(__file__).resolve().parent.parent / "prompts" / "consult.json").pick


def T(t, c, sure=True):
    return {"table": t, "code": c, "sure": sure}


def acc_of(targets, kind="new", sites=None, withdraw=None, obj=None, first=False, prev=None, kinds=None):
    return vf.verify({"targets": targets, "kind": kind, "sites": sites or [], "withdraw": withdraw or [],
                      "kinds": kinds or []}, EL, obj if obj is not None else cst.empty(), first_turn=first,
                     talk_streak=0, talk_max=3, prev_row=prev)


def apply(obj, acc, n, prev_ask=None):
    return cst.update(obj, cst.observe_accepted(acc, prev_ask, obj), [k for k in acc.kinds if k in cst.WISH_KINDS],
                      n, set(acc.withdraw))


# ───── S8-T6 객체 ─────

def test_s8_t6_sure_said_guess_and_source():
    obj = cst.empty()
    apply(obj, acc_of([T("concern", "ZZC-1"), T("procedure", "ZZP-1", False)]), 1)
    c, p = obj["concerns"][0], obj["subjects"][0]
    assert c["state"] == "said" and p["state"] == "guess"
    assert c["source"] == {"turn": 1, "how": "text", "from_ask": None, "sure": True}  # OU12 — noul · decision · matched 없음
    assert obj["focus"] == "ZZC-1" and cst.axis_items(obj, "subjects") == []  # guess 는 축 아님


def test_s8_t6_option_answer_how_and_follow_keep():
    obj = cst.empty()
    apply(obj, acc_of([T("site", "ZZS-1")]), 1)
    ask = {"slot": "concern", "about": "site:ZZS-1", "turn": 1,
           "options": [{"table": "concern", "code": "ZZC-1", "name": "가짜고민"}]}
    apply(obj, acc_of([T("concern", "ZZC-1")]), 2, prev_ask=ask)
    assert obj["concerns"][0]["source"]["how"] == "option" and obj["concerns"][0]["source"]["from_ask"] == 1
    apply(obj, acc_of([T("procedure", "ZZP-1", False)]), 3)
    diff = apply(obj, acc_of([T("procedure", "ZZP-1")], kind="follow", obj=obj), 4)  # OU4 — 객체 안 대상은 급 그대로
    assert obj["subjects"][0]["state"] == "guess" and not [x for x in diff if x["op"] == "state"]
    assert obj["subjects"][0]["history"][-1]["turn"] == 4
    apply(obj, acc_of([T("product", "ZZR-1")], kind="follow", obj=obj), 5)  # 객체 밖 대상 → said
    assert cst.find(obj, "product", "ZZR-1")["state"] == "said"


def test_s8_t6_focus_is_last_in_targets_order():
    a, b = T("concern", "ZZC-1"), T("concern", "ZZC-2")
    obj = cst.empty()
    apply(obj, acc_of([a, b]), 1)
    assert obj["focus"] == "ZZC-2"
    obj = cst.empty()
    apply(obj, acc_of([b, a]), 1)
    assert obj["focus"] == "ZZC-1"  # 앱이 다시 정렬하지 않는다
    assert any("말에 나온 차례대로" in x for x in PICK["rules"])  # 규칙 1


def test_s8_t6_withdraw_and_talk_only_withdraws():
    obj = cst.empty()
    apply(obj, acc_of([T("concern", "ZZC-1"), T("procedure", "ZZP-1")]), 1)
    acc = acc_of([T("concern", "ZZC-2")], kind="talk", withdraw=["procedure:ZZP-1"], obj=obj)
    diff = cst.update(obj, [], [], 2, set(acc.withdraw))  # talk — 관찰 0 · 거둠만
    assert [x["op"] for x in diff] == ["withdraw"] and obj["subjects"][0]["state"] == "withdrawn"
    assert cst.find(obj, "concern", "ZZC-2") is None


def test_same_name_site_pairs_with_concern():
    obj = cst.empty()
    acc = acc_of([T("concern", "ZZC-9"), T("site", "ZZS-9")], sites=["ZZS-9"])
    apply(obj, acc, 1)
    assert cst.find(obj, "site", "ZZS-9")["source"]["pair_concern"] == "ZZC-9"


# ───── S8-T7 부위 잇기(OU7) ─────

def row(n, targets, *, ask=None, sites=None, err=None, uerr=None):
    return {"turn": n, "error": err, "consult": {"understand": {"accepted": {"targets": targets}, "error": uerr},
                                                "ask": ask, "site_filter": {"sites": sites or []}}}


SITE_ROW = row(1, [T("site", "ZZS-1")], ask={"slot": "concern", "about": "site:ZZS-1"}, sites=["ZZS-1"])


def dec(acc, turns):
    return sf.decide(acc, cst.empty(), sf.prev_ok(turns), None)


def test_s8_t7_carry_when_all_four_hold():
    got = dec(acc_of([T("concern", "ZZC-1")]), [SITE_ROW])
    assert got["sites"] == ["ZZS-1"] and got["carried"] == ["ZZS-1"] and got["from"] == 1


def test_s8_t7_not_carried_cases():
    no_ask = row(1, [T("site", "ZZS-1")], sites=["ZZS-1"])
    other = row(1, [T("site", "ZZS-1")], ask={"slot": "site", "about": "site:ZZS-1"}, sites=["ZZS-1"])
    with_concern = row(1, [T("site", "ZZS-1"), T("concern", "ZZC-2")], ask={"slot": "concern", "about": "site:ZZS-1"})
    assert dec(acc_of([T("concern", "ZZC-1")]), [no_ask])["carried"] == []  # 되물음 없음(focus 있던 꼴)
    assert dec(acc_of([T("concern", "ZZC-1")]), [other])["carried"] == []  # 다른 slot
    assert dec(acc_of([T("concern", "ZZC-1")]), [with_concern])["carried"] == []  # 앞 턴에 고민
    assert dec(acc_of([T("concern", "ZZC-1")], sites=["ZZS-2"]), [SITE_ROW])["carried"] == []  # 이번 턴 부위
    assert dec(acc_of([T("concern", "ZZC-1"), T("procedure", "ZZP-1")]), [SITE_ROW])["carried"] == []  # 고민 말고도
    two_back = [SITE_ROW, row(2, [T("procedure", "ZZP-1")])]
    assert dec(acc_of([T("concern", "ZZC-1")]), two_back)["carried"] == []  # 두 턴 앞


def test_s8_t7_failed_turn_skipped_and_sites_only_from_targets():
    failed = row(2, [], uerr={"kind": "timeout"})
    assert dec(acc_of([T("concern", "ZZC-1")]), [SITE_ROW, failed])["from"] == 1  # 실패 턴은 건너뛴다
    by_targets = row(1, [T("site", "ZZS-1")], ask={"slot": "concern", "about": "site:ZZS-1"}, sites=[])
    assert dec(acc_of([T("concern", "ZZC-1")]), [by_targets])["carried"] == ["ZZS-1"]  # sites=[] 인 「부위만」


# ───── S8-T8 부위 거르개(객체 · 거르개 몫) ─────

def test_s8_t8_filter_is_this_turn_sites_only():
    obj = cst.empty()
    a = acc_of([T("concern", "ZZC-1"), T("site", "ZZS-1")], sites=["ZZS-1"])
    apply(obj, a, 1)
    assert sf.decide(a, obj, None, obj["focus"])["sites"] == ["ZZS-1"]
    a = acc_of([T("concern", "ZZC-1"), T("site", "ZZS-1")], sites=[])  # 앞 턴 부위를 다시 든 꼴
    apply(obj, a, 2)
    assert sf.decide(a, obj, None, obj["focus"])["sites"] == [] and cst.find(obj, "site", "ZZS-1")["state"] in cst.AXIS
    a = acc_of([T("concern", "ZZC-1")], sites=["ZZS-2"])  # sites 에만 → targets 에 더함 · 거르개
    assert a.added_from_sites == ["ZZS-2"] and sf.decide(a, obj, None, obj["focus"])["sites"] == ["ZZS-2"]


def test_s8_t8_pair_site_only_with_pair_focus():
    obj = cst.empty()
    a = acc_of([T("concern", "ZZC-9"), T("site", "ZZS-9")], sites=["ZZS-9"])
    apply(obj, a, 1)
    got = sf.decide(a, obj, None, "ZZC-9")
    assert got["sites"] == ["ZZS-9"] and got["pair_kept"] == ["ZZS-9"]
    got = sf.decide(a, obj, None, "ZZC-1")
    assert got["sites"] == [] and got["pair_dropped"] == ["ZZS-9"]
