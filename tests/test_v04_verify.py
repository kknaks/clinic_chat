"""S8-T4 · S8-T5 — 검증(SPEC-008 VF1 ~ VF12 · AR-6 · WORK-017 WP-3). 외부 호출 0."""

from __future__ import annotations

import pytest

from clinic_chat.consult import entry_list as el_mod
from clinic_chat.consult import state as cst
from clinic_chat.consult import verify as vf
from tests.v04_fixture import entry_rows

EL = el_mod.build(entry_rows(), "fakehash", aliases=True)


def V(parsed, obj=None, *, first=False, streak=0, prev=None):
    return vf.verify(parsed, EL, obj or cst.empty(), first_turn=first, talk_streak=streak, talk_max=3, prev_row=prev)


def T(t, c, sure=True):
    return {"table": t, "code": c, "sure": sure}


def test_vf1_parse():
    assert vf.parse('앞 글 {"kind":"new"} 뒤')["kind"] == "new"
    for bad in ("", "없음", "{깨짐", "[1,2]"):
        with pytest.raises(vf.UnderstandJSONError):
            vf.parse(bad)


def test_s8_t4_drop_not_in_gold_bad_shape_outside_tables_and_keep_rest():
    a = V({"targets": [T("concern", "ZZC-404"), T("adverse", "ZZA-1"), "글", {"table": 1}, T("concern", "ZZC-1")],
           "kind": "new"})
    assert [x["code"] for x in a.targets] == ["ZZC-1"]
    why = sorted((x["code"], x["why"]) for x in a.dropped if x.get("code"))
    assert ("ZZC-404", "not_in_gold") in why and ("ZZA-1", "not_in_gold") in why
    assert sum(1 for x in a.dropped if x["why"] == "bad_shape") == 2
    assert V({"targets": [T("concern", "ZZC-404")], "kind": "new"}).targets == []  # 전부 버려짐 → QM3(턴 쪽)


def test_s8_t5_dup_merge_and_same_name_concern_site():
    a = V({"targets": [T("concern", "ZZC-1"), T("concern", "ZZC-1", False)], "kind": "new"})
    assert a.targets == [{"table": "concern", "code": "ZZC-1", "name": "가짜고민", "sure": False}]  # VF3
    assert any(x["why"] == "dup" for x in a.dropped)
    a = V({"targets": [T("procedure", "ZZP-7"), T("product", "ZZR-7"), T("modality", "ZZM-1")], "kind": "new"})
    assert [x["code"] for x in a.targets] == ["ZZM-1"]  # VF4 — 계열 > 시술 > 제품(공백 무시 · 소문자)
    assert a.merged == [{"kept": "modality:ZZM-1", "folded": ["procedure:ZZP-7", "product:ZZR-7"]}]
    a = V({"targets": [T("concern", "ZZC-9"), T("site", "ZZS-9")], "kind": "new", "sites": ["ZZS-9"]})
    assert {x["table"] for x in a.targets} == {"concern", "site"} and not a.merged  # VF5


def test_s8_t5_kind_values():
    assert (V({"kind": "뭐"}).kind, V({"kind": "뭐"}).kind_from) == ("new", "bad_value")
    a = V({"targets": [T("concern", "ZZC-1")], "kind": "talk"})
    assert a.targets == [] and a.dropped[0]["why"] == "talk"  # VF6
    a = V({"kind": "follow"}, first=True)
    assert (a.kind, a.kind_from) == ("new", "first_follow")  # VF7
    assert V({"kind": "talk"}, first=True).kind == "talk"
    a = V({"targets": [T("concern", "ZZC-1")], "kind": "talk"}, streak=3)
    assert (a.kind, a.kind_from) == ("new", "talk_max") and a.targets  # VF11 — new 로 읽은 턴은 대상을 버리지 않는다


def test_s8_t5_follow_empty_takes_previous_target():
    obj = cst.empty()
    obj["subjects"].append({"table": "procedure", "code": "ZZP-1", "name": "가짜시술", "state": "said",
                            "history": [{"turn": 1, "state": "said"}]})
    a = V({"targets": [], "kind": "follow"}, obj)
    assert a.targets == [{"table": "procedure", "code": "ZZP-1", "name": "가짜시술", "sure": True}]
    assert a.follow_target == {"table": "procedure", "code": "ZZP-1"}


def test_s8_t5_sites_withdraw_kinds_out():
    a = V({"targets": [T("concern", "ZZC-1")], "kind": "new", "sites": ["ZZS-1", "ZZC-1", "ZZS-404"]})
    assert a.sites == ["ZZS-1"] and a.added_from_sites == ["ZZS-1"]  # VF8 ⓐ ⓑ
    assert a.targets[-1] == {"table": "site", "code": "ZZS-1", "name": "가짜부위", "sure": True}
    obj = cst.empty()
    obj["subjects"].append({"table": "procedure", "code": "ZZP-1", "name": "가짜시술", "state": "said"})
    a = V({"targets": [T("procedure", "ZZP-1"), T("concern", "ZZC-1")], "kind": "new",
           "withdraw": ["procedure:ZZP-1", "concern:ZZC-2", "글"]}, obj)
    assert a.withdraw == ["procedure:ZZP-1"]  # VF9 — 객체에 살아 있는 것만
    assert [x["code"] for x in a.targets] == ["ZZC-1"]  # 거둠이 앞선다
    a = V({"kind": "new", "kinds": ["package", "same_day", "없는것", "same_day", "overview"], "out": "예",
           "targets": [T("concern", "ZZC-1", "응")]})
    assert a.kinds == ["overview", "package", "same_day"]  # VF10 — walk/kinds.py KIND_CODES 순
    assert a.out is False and a.targets[0]["sure"] is False


def test_ar6_accepted_targets_all_enter_object():
    """AR-6 — 검증 뒤 대상은 전부 객체 diff 에 든다(WP-4 와 함께)."""
    obj = cst.empty()
    a = V({"targets": [T("concern", "ZZC-1"), T("site", "ZZS-1", False), T("procedure", "ZZP-1")], "kind": "new",
           "sites": ["ZZS-2"]}, obj)
    diff = cst.update(obj, cst.observe_accepted(a, None, obj), [], 1, set(a.withdraw))
    added = {(x["table"], x["code"]) for x in diff if x["op"] == "add"}
    assert added == {(x["table"], x["code"]) for x in a.targets}


def test_l1_withdraw_only_new_reads_as_talk():
    obj = cst.empty()
    obj["subjects"].append({"table": "procedure", "code": "ZZP-1", "name": "가짜시술", "state": "said"})
    a = V({"targets": [], "kind": "new", "withdraw": ["procedure:ZZP-1"]}, obj)
    assert (a.kind, a.kind_from, a.withdraw) == ("talk", "withdraw_only", ["procedure:ZZP-1"])
    assert V({"targets": [], "kind": "new"}).kind == "new"  # 거둠도 없으면 그대로(QM3)
