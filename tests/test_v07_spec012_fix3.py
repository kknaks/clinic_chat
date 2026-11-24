"""SPEC-012 고침 3 — 재검수 r3 P2-1 ~ P2-6(목록 안 케이스 = 같은 말 · 밖 패키지 이름만 · 고르기 포함 관계 양쪽 ·
명동점 없음 사실 줄 · 물은 kinds · 늘 읽은 안전 표지). 외부 호출 0."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from clinic_chat.consult import fetch_read as fr
from clinic_chat.consult import material as mat
from clinic_chat.consult import turn as turn_mod
from tests.test_v07_spec012_fix2 import _det

PROMPT = json.loads(open("prompts/consult.json", encoding="utf-8").read())


# ---------- P2-3 고르기 포함 관계 양쪽(좁은 쪽이 이김) ----------


def test_pick_named_subset_of_product_narrows():
    """S12 T4 「눈가는 아이써마지로 할래」 — 이름 일치(아이써마지 2) ⊂ 제품 써마지(5) → 이름 집합만(제품의 시술은 넓히지 않음)."""
    d = _det({"PRD-THM": {"E1", "E2", "F1", "F2", "F3"}, "PRC-RF": {"E1", "E2", "F1", "F2", "F3", "R9"}})
    conds = {"procedure": [{"table": "name", "code": "kos_procedure:아이써마지", "name": "아이써마지", "items": ["E1", "E2"]},
                           {"table": "procedure", "code": "PRC-RF", "name": "고주파", "from": "product"}],
             "product": [{"table": "product", "code": "PRD-THM", "name": "써마지", "sure": False}]}
    res = asyncio.run(d.items(conds))
    assert set(res.codes) == {"E1", "E2"} and res.per_slot.get("pick_narrowed") == 1


def test_pick_union_unchanged_when_no_inclusion():
    """포함 관계가 없으면 고침 2 합집합 그대로(S06 T4 꼴) · 제품 ⊆ 시술이면 제품이 좁힘(보톡스 ⊇ 하이톡스) 그대로."""
    d = _det({"PRD-POT": {"P1", "P2"}, "PRD-HTS": {"B2"}, "PRC-BTX": {"B1", "B2", "B3"}})
    a = {"procedure": [{"table": "name", "code": "item:티타늄", "name": "티타늄리프팅", "items": ["T1", "T2"]}],
         "product": [{"table": "product", "code": "PRD-POT", "name": "포트라"}]}
    assert set(asyncio.run(d.items(a)).codes) == {"T1", "T2", "P1", "P2"}
    b = {"procedure": [{"table": "procedure", "code": "PRC-BTX", "name": "보톡스"}],
         "product": [{"table": "product", "code": "PRD-HTS", "name": "하이톡스"}]}
    assert asyncio.run(d.items(b)).codes == ["B2"]


# ---------- P2-1 목록 안 케이스 = 같은 말 ----------


def _turn(question, topic):
    t = turn_mod.Consult.__new__(turn_mod.Consult)
    t.question = question
    t._cur_topic = lambda: topic
    return t


KOS_CASE = SimpleNamespace(
    by_case={"CAS-9": ("J1", "J2"), "CAS-OUT": ("Z1",)},
    item={"J1": {"hub": {"procedure_codes": ["PRC-1"], "site_codes": ["S-JAW"]}},
          "J2": {"hub": {"procedure_codes": ["PRC-1"], "site_codes": ["S-JAW"]}},
          "Z1": {"hub": {"procedure_codes": ["PRC-1"], "site_codes": ["S-JAW"]}}})
TOPIC = {"items": ["J1", "J2", "K1", "K2"],
         "conditions": {"procedure": [{"table": "procedure", "code": "PRC-1", "name": "보톡스"}],
                        "site": [{"table": "site", "code": "S-JAW", "name": "턱"}]}}
CASE = {"table": "procedure_case", "code": "CAS-9", "name": "사각턱보톡스", "sure": True}
SITE = {"table": "site", "code": "S-JAW", "name": "턱", "sure": True}


def test_case_inside_list_is_same_utterance():
    """S10 T2 「턱 보톡스 맞고 싶다고」 — 1차가 케이스를 내도 목록 안 · 시술 · 부위가 주제 값과 같으면 주제 시술 값으로(같은 목록)."""
    out, rec = asyncio.run(_turn("턱 보톡스 맞고 싶다고", TOPIC)._case_same_targets(KOS_CASE, [SITE, CASE]))
    assert [(x["table"], x["code"]) for x in out] == [("site", "S-JAW"), ("procedure", "PRC-1")]
    assert out[1]["from"] == "case_same" and rec == [{"case": "CAS-9", "to": ["PRC-1"]}]


def test_case_kept_when_named_or_outside_list():
    """케이스 이름을 그대로 말했으면(규칙 13) · 케이스 진료항목이 목록 밖이면 · 주제 시술이 다르면 케이스 그대로(좁히기)."""
    named, rec = asyncio.run(_turn("사각턱보톡스로 할래", TOPIC)._case_same_targets(KOS_CASE, [SITE, CASE]))
    assert CASE in named and not rec
    out_case = {**CASE, "code": "CAS-OUT"}
    outside, rec2 = asyncio.run(_turn("턱 보톡스", TOPIC)._case_same_targets(KOS_CASE, [SITE, out_case]))
    assert out_case in outside and not rec2
    other = {**TOPIC, "conditions": {**TOPIC["conditions"], "procedure": [{"table": "procedure", "code": "PRC-2"}]}}
    diff, rec3 = asyncio.run(_turn("턱 보톡스", other)._case_same_targets(KOS_CASE, [SITE, CASE]))
    assert CASE in diff and not rec3


# ---------- P2-2 매핑 밖 패키지 = 이름만 ----------


def test_package_target_line_names_only_in_branch_scope():
    """after2 S06 T1 — 효과 줄의 대상이 매핑 밖 패키지(PKG-0069)면 `scope = branch` 에서 이름만(HN1 · R17)."""
    kos = SimpleNamespace(by_case={}, by_proc={"PRC-1": ()}, by_product={}, items=[{"hub": {}}])
    md = fr.md_sets(kos)
    assert md["package"] == set()
    ln = fr.Line(key="GOU-1", kinds=["outcome"], layer=None, silver_table="outcome", silver_id="OUT-1",
                 row={"target_table": "package", "target_code": "PKG-0069", "target_name": "가짜프로그램"})
    ctx = fr.Ctx(targets=set(), question="얼굴 타이트닝 시술 뭐 있어", md=md, g2={"outcome"})
    assert fr.decide([ln], fr.Plan(scope="branch"), ctx)["GOU-1"] == (fr.NAMES, "branch")
    assert fr.decide([ln], fr.Plan(scope="all"), ctx)["GOU-1"][0] == fr.FULL  # 「다른 병원」 · 밖을 물으면 상세


# ---------- P2-4 명동점 없음 = 블록 사실 줄 ----------


def test_out_names_parsed_and_absent_line_first():
    from clinic_chat.consult import kos_block as kb
    from clinic_chat.consult import verify as vf
    from tests.test_v05_llm_block import KOS, kos

    a = vf.Accepted()
    assert a.out_names == [] and "out_names" in a.view()
    assert '"out_names":[]' in PROMPT["pick"]["rules"][1]  # 1차 출력 꼴
    k = kos()
    base = dict(cond={"unmatched": True}, items=[], cart=[], cart_rec=None, viewed=[], pairs={}, pairs_capped=False,
                course=[], figs=[], sent={"codes": set(), "cart_sig": "", "pairs": set()}, items_max=30)
    text, meta = kb.build(k, KOS, **base, absent=[{"name": "가짜이식", "branch_items": 0}])
    lines = text.split("\n")
    assert lines[1].startswith(KOS["labels"]["absent"]) and "가짜이식" in lines[1] and lines[2].startswith(KOS["labels"]["conditions"])
    assert meta["absent"] == [{"name": "가짜이식", "branch_items": 0}]
    text0, meta0 = kb.build(k, KOS, **base)
    assert KOS["labels"]["absent"] not in text0 and meta0["absent"] == []


def test_verify_out_names_strings_only():
    from clinic_chat.consult import verify as vf

    el = SimpleNamespace(has=lambda t, c: False, name=lambda t, c: "", gold_hash="")
    parsed = {"targets": [], "kind": "new", "sites": [], "withdraw": [], "kinds": [], "out": True,
              "out_names": ["가짜이식", " 가짜이식 ", "", 3, None], "topic": {"move": "new", "to": None}}
    a = vf.verify(parsed, el, {}, first_turn=False, talk_streak=0, talk_max=3, prev_row=None)
    assert a.out_names == ["가짜이식"]


# ---------- P2-6 물은 kinds · 늘 읽은 안전 표지 ----------


def _fake_walk(by: dict):
    lines = [{"n": 1, "gid": "G-A", "kind": ["adverse"]}, {"n": 2, "gid": "G-I", "kind": ["indication"]}]
    groups = [{"table": "g_adverse", "same": {"target_name": "가짜"}, "marks": {}, "rows": [{"n": 1, "x": "a"}]},
              {"table": "g_indication", "same": {"target_name": "가짜"}, "marks": {}, "rows": [{"n": 2, "x": "i"}]}]
    return SimpleNamespace(material=SimpleNamespace(lines=lines, groups=groups), reads=[], verified=None,
                           front=SimpleNamespace(by=by))


def test_safety_always_group_marked():
    tm = mat.renumber(_fake_walk({"adverse": "safety_always", "indication": "understand"}), {}, {}, 1)
    marks = {g["table"]: g["marks"] for g in tm.groups}
    assert marks["g_adverse"].get("by") == "safety_always" and "by" not in marks["g_indication"]
    asked = mat.renumber(_fake_walk({"adverse": "understand"}), {}, {}, 1)  # 물어서 읽은 안전은 표지 없음
    assert all("by" not in g["marks"] for g in asked.groups)


def test_prompt_r25_and_r22_principles_only():
    rules = PROMPT["kos"]["rules"]
    r25 = next(r for r in rules if r.startswith("R25."))
    r22 = next(r for r in rules if r.startswith("R22."))
    assert "asked_kinds" in r25 and "safety_always" in r25 and "absent" in r22 and "branch_items" in r22
    assert "「" not in r25 and "「" not in r22  # 따옴 예문 0
