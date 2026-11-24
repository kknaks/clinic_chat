"""SPEC-012 고침 4 — after3 하락 셋 · S08 T3(받아도 되나 = 안전 물음 원칙 · 명동점 이름 out 거둠 · 계열 주제 케이스 = 같은 말 ·
진료항목 줄 매핑 제품). 외부 호출 0."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from clinic_chat.consult import kos_block as kb
from clinic_chat.consult import turn as turn_mod
from clinic_chat.consult.verify import Accepted
from tests.test_v07_spec012_fix3 import CASE, KOS_CASE, SITE, _turn

PROMPT = json.loads(open("prompts/consult.json", encoding="utf-8").read())


def _rule(prefix: str) -> str:
    return next(r for r in PROMPT["pick"]["rules"] if r.startswith(prefix))


# ---------- 1. S10 T1 「맞아도돼 ?」 = 안전 물음(원칙 · 판단은 1차 소넷) ----------


def test_pick_rules_may_i_receive_is_safety():
    """고침 4 · 마감(r4 P2-1) — 원칙만(시나리오 말 예문 0) · 규칙 11 과 20-2 의 꼴 (가) · (나) · (다)가 같다 · R25 와 맞물림."""
    r11, r20 = _rule("11."), _rule("20-2.")
    assert "(가)" in r11 and "안전 확인 — contraindication, adverse" in r11
    assert "(나)" in r11 and "효과 확인 — outcome, indication" in r11
    assert "(다)" in r11 and "안전 종류를 더하지 않고" in r11 and "비우지 않음" in r11
    for k in ("(가) 안전 확인", "(나) 효과 확인", "(다) 있음 · 선택지 확인"):
        assert k in r20
    for bad in ("맞아도 돼", "받을 수 있어", "가능해?", "받아도 돼", "해도 되나"):  # 시나리오 말 그대로 예문 없음
        assert bad not in r11 and bad not in r20
    r25 = next(r for r in PROMPT["kos"]["rules"] if r.startswith("R25."))
    assert "asked_kinds 에 안전 종류가 있으면" in r25


# ---------- 2. S06 T4 명동점 이름이면 out 거둠(데이터) ----------


def _kos_names(*names):
    items = [{"kos_item_code": f"I{i}", "kos_item_name": n} for i, n in enumerate(names)]
    return SimpleNamespace(items=items, groups=[], sort_codes=lambda xs: sorted(xs))


def _t(kos):
    t = turn_mod.Consult.__new__(turn_mod.Consult)
    t.ctx = SimpleNamespace(kos=kos)
    return t


def test_out_cleared_when_all_out_names_are_branch_names():
    kos = _kos_names("티타늄리프팅", "포트라")
    a = Accepted(out=True, out_names=["티타늄리프팅"])
    _t(kos)._clear_out_named(a)
    assert a.out is False and a.out_names == [] and a.out_cleared == ["티타늄리프팅"]
    assert a.view()["out_cleared"] == ["티타늄리프팅"]


def test_out_kept_when_any_name_not_branch_or_partial():
    kos = _kos_names("티타늄리프팅", "리프팅")
    mixed = Accepted(out=True, out_names=["티타늄리프팅", "모발이식"])
    _t(kos)._clear_out_named(mixed)
    assert mixed.out is True and mixed.out_names == ["티타늄리프팅", "모발이식"] and not mixed.out_cleared
    part = Accepted(out=True, out_names=["울쎄라리프팅"])  # 이름 일부(리프팅)만 일치 = 명동점 이름 아님
    _t(kos)._clear_out_named(part)
    assert part.out is True and not part.out_cleared


# ---------- 3. S10 T2 주제 시술 칸 = 계열일 때 케이스 = 같은 말 ----------


class _Det:
    def __init__(self, sets, fail=False):
        self.sets, self.fail = sets, fail

    async def modality(self, code):
        if self.fail:
            raise RuntimeError("hub")
        return self.sets.get(code, set())


MOD_TOPIC = {"items": ["J1", "J2", "K1"],
             "conditions": {"procedure": [{"table": "modality", "code": "M1", "name": "보톡스"}],
                            "site": [{"table": "site", "code": "S-JAW", "name": "턱"}]}}


def test_case_under_modality_topic_is_same_utterance():
    det = _Det({"M1": {"J1", "J2", "K1", "Z9"}})
    out, rec = asyncio.run(_turn("턱 보톡스 맞고 싶다고", MOD_TOPIC)._case_same_targets(KOS_CASE, [SITE, CASE], det))
    assert [(x["table"], x["code"]) for x in out] == [("site", "S-JAW"), ("modality", "M1")]
    assert out[1]["from"] == "case_same" and rec == [{"case": "CAS-9", "to": ["modality:M1"]}]
    both, _ = asyncio.run(_turn("턱 보톡스 맞고 싶다고", MOD_TOPIC)._case_same_targets(
        KOS_CASE, [{"table": "modality", "code": "M1", "name": "보톡스"}, SITE, CASE], det))
    assert [(x["table"], x["code"]) for x in both] == [("modality", "M1"), ("site", "S-JAW")]  # 계열 한 번


def test_case_kept_under_modality_when_not_below_or_hub_error():
    out, rec = asyncio.run(_turn("턱 보톡스", MOD_TOPIC)._case_same_targets(KOS_CASE, [SITE, CASE], _Det({"M1": {"K1"}})))
    assert CASE in out and not rec
    out2, rec2 = asyncio.run(_turn("턱 보톡스", MOD_TOPIC)._case_same_targets(KOS_CASE, [SITE, CASE], _Det({}, fail=True)))
    assert CASE in out2 and not rec2
    named, rec3 = asyncio.run(_turn("사각턱보톡스로", MOD_TOPIC)._case_same_targets(
        KOS_CASE, [SITE, CASE], _Det({"M1": {"J1", "J2"}})))
    assert CASE in named and not rec3


# ---------- 4. S08 T3 진료항목 줄에 매핑 제품 ----------


def test_item_line_has_mapped_products():
    kos = SimpleNamespace(
        item={"KR": {"kos_item_name": "턱보톡스(국산)", "kos_medicine": "보톡스",
                     "hub": {"products": [{"code": "PRD-022", "name": "하이톡스"}], "procedure_codes": ["PRC-001"]}}},
        covers_of={}, option_product={}, option={})
    line = kb.item_line(kos, "KR")
    assert line["medicine"] == "보톡스" and line["products"] == ["하이톡스"]


# ---------- 고침 5(코디 ① ~ ③) ----------

from tests.test_v04_turn import understand_calls  # noqa: E402
from tests.state_fixture import Run  # noqa: E402
from tests.test_v07_spec012_topics import FakeDet, FakeKos, V  # noqa: E402
from tests.v04_fixture import out, set_seq  # noqa: E402


def test_understand_json_broken_retried_once_ok(tmp_path, fake_cli):
    """① 1차 출력 JSON 이 깨지면 같은 입력으로 한 번 다시 부른다 — 둘째가 맞으면 그 턴은 정상(장부 retry · 토큰 두 호출 합)."""
    r = Run(tmp_path, fake_cli)
    try:
        set_seq(fake_cli[1], [out(raw='{"targets":[],"}'), out([("concern", "ZZC-1")], kinds=["indication"])])
        row, kinds, _ = r.turn("가짜고민 시술")
        u = row["consult"]["understand"]
        assert u["error"] is None and u["retry"]["kind"] == "json" and u["retry"]["ok"] is True
        assert u["retry"]["first_raw"].startswith('{"targets"') and len(understand_calls(r.log)) == 2
        assert "list" in kinds
    finally:
        r.close()


def test_usage_sum():
    assert turn_mod._usage_sum({"input_tokens": 3, "output_tokens": None}, {"input_tokens": 4, "output_tokens": 2}) == {
        "input_tokens": 7, "output_tokens": 2}


def test_hs1_narrowest_sub_concern_wins():
    """② S03 T2 r1 — 주름(위 고민) + 눈 주변 · 입꼬리: 고민 부위가 그 부위 하나뿐인 하위 고민만(자손 · 더 넓은 하위 고민은 뺌)
    — 1차가 하위 고민을 바로 낸 것과 같은 주제 조건."""

    class Det2(FakeDet):
        CS = {**FakeDet.CS, "C-EYEUNDER": ["S-EYEUNDER"], "C-MARI": ["S-MOUTH", "S-MARI"]}

        async def concern(self, code, res):
            if code == "C-GEN":
                res.upper.extend(["C-EYEW", "C-EYEUNDER", "C-MARI", "C-MOUTHW", "C-AGING"])
            return set()

    class Kos2(FakeKos):
        sites = {**FakeKos.sites, "S-EYEUNDER": {"parent_code": "S-EYE"}, "S-MARI": {"parent_code": "S-FACE"}}

        def ancestors(self, s):
            out_, c = [], self.sites.get(s, {}).get("parent_code")
            while c:
                out_.append(c)
                c = self.sites.get(c, {}).get("parent_code")
            return out_

    async def noop(*a, **k):
        return None

    me = SimpleNamespace(_cur_topic=lambda: None, el=SimpleNamespace(name=lambda t, c: c), _product_procedures=noop)
    targets = [V("S-EYE", "눈 주변"), V("S-MOUTH", "입꼬리"), V("C-GEN", "주름")]
    acc = SimpleNamespace(targets=targets, pairs=[], country=None, exclude=[])
    subs, _ = asyncio.run(turn_mod.Consult._split_subs(me, Kos2(), Det2(), acc, targets))
    assert [[v["code"] for v in s["turn"]["concern"]] for s in subs] == [["C-EYEW"], ["C-MOUTHW"]]


class _PDet:
    def __init__(self, sets, fail=False):
        self.sets, self.fail = sets, fail

    async def product(self, code):
        if self.fail:
            raise RuntimeError("hub")
        return self.sets.get(code, set())


def _pturn(q):
    t = turn_mod.Consult.__new__(turn_mod.Consult)
    t.question = q
    items = {("product", "P-THM"): SimpleNamespace(name="써마지"), ("product", "P-THMF"): SimpleNamespace(name="써마지FLX"),
             ("procedure", "R-RF"): SimpleNamespace(name="고주파")}
    t.el = SimpleNamespace(items=items, name=lambda tb, c: items[(tb, c)].name if (tb, c) in items else "")
    return t


P_KOS = SimpleNamespace(item={"T1": {"hub": {"procedure_codes": ["R-RF"]}}, "T2": {"hub": {"procedure_codes": ["R-RF"]}}})
EYE = {"table": "site", "code": "S-EYE", "sure": True}
RF = {"table": "procedure", "code": "R-RF", "sure": False}


def test_product_named_replaces_procedure():
    """③ S04 T4 r2 — 「눈가에는 써마지 받고 싶어서」 에 1차가 시술 고주파를 내면 말한 제품 써마지로(이름 일치 · 데이터)."""
    out_, rec = asyncio.run(_pturn("아 눈가에는 써마지 받고 싶어서")._product_named_targets(
        P_KOS, [EYE, RF], _PDet({"P-THM": {"T1", "T2"}})))
    assert [(x["table"], x["code"]) for x in out_] == [("site", "S-EYE"), ("product", "P-THM")]
    assert rec == [{"procedure": "R-RF", "to": "P-THM", "name": "써마지"}]
    longer, rec2 = asyncio.run(_pturn("써마지FLX 받고 싶어")._product_named_targets(
        P_KOS, [RF], _PDet({"P-THM": {"T1"}, "P-THMF": {"T2"}})))
    assert [x["code"] for x in longer] == ["P-THMF"]  # 겹치면 긴 이름


def test_product_named_kept_when_procedure_said_or_no_items():
    said, rec = asyncio.run(_pturn("써마지 말고 고주파로")._product_named_targets(
        P_KOS, [{**RF, "name": "고주파"}], _PDet({"P-THM": {"T1"}})))
    assert said[0]["table"] == "procedure" and not rec
    none, rec2 = asyncio.run(_pturn("써마지")._product_named_targets(P_KOS, [RF], _PDet({})))
    assert none == [RF] and not rec2
    err, rec3 = asyncio.run(_pturn("써마지")._product_named_targets(P_KOS, [RF], _PDet({}, fail=True)))
    assert err == [RF] and not rec3


def test_product_named_skips_generic_and_covered_names():
    """마감(r4 P2-5) — 「보톡스」(제품 이름 = 시술 이름 · 일반 이름)는 바꾸지 않음 · 「아이써마지」(KOS 시술 이름) 안의 「써마지」 는
    더 긴 이름이 덮어 제품 이름으로 안 셈(모든 목록 이름에서 긴 이름이 이김)."""
    t = _pturn("턱 보톡스 맞고 싶다고")
    t.el.items[("product", "P-BTX")] = SimpleNamespace(name="보톡스")
    t.el.items[("procedure", "R-BTX")] = SimpleNamespace(name="보톡스")
    kos = SimpleNamespace(item={"B1": {"hub": {"procedure_codes": ["R-OTHER"]}}}, items=[], groups=[])
    out_, rec = asyncio.run(t._product_named_targets(kos, [{"table": "procedure", "code": "R-OTHER"}],
                                                     _PDet({"P-BTX": {"B1"}})))
    assert out_ == [{"table": "procedure", "code": "R-OTHER"}] and not rec
    eye = _pturn("눈가는 아이써마지로 할래")
    kos2 = SimpleNamespace(item=P_KOS.item, items=[], groups=[{"kos_procedure": "아이써마지", "items": ["T1"]}])
    out2, rec2 = asyncio.run(eye._product_named_targets(kos2, [RF], _PDet({"P-THM": {"T1", "T2"}})))
    assert out2 == [RF] and not rec2
