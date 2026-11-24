"""SPEC-012 0.1.2 WP-1 — 입구 · 주제 상태(HQ0 ~ 2 · HU1 · HL1 · HP1 · HW1 · HS2 · HA1 · HX1) + §3-D 결정 칸 연속 재생.

외부 호출 0(장난감 셈 · 가짜 허브 · 가짜 매핑). 값 이름은 실물 시나리오(quality/scenarios.json)를 따라 적었다.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from clinic_chat.consult import ask_axis as ax
from clinic_chat.consult import topics as tp
from clinic_chat.consult.turn import Consult

# 장난감 명동점 진료항목 번호 — 칸 값마다 닿는 진료항목
U = {
    "R-THM": {1, 2, 3, 4, 5},  # 써마지(써마지FLX 1 ~ 3 · 아이써마지 4 · 5)
    "P-RF": {1, 2, 3, 4, 5},  # 고주파(써마지 제품의 시술)
    "R-ULT": {10, 11},  # 울쎄라
    "P-HIFU": {10, 11, 12},  # HIFU
    "R-SOF": {12},  # 소프웨이브
    "S-BOL": {1, 2, 3, 10, 11, 12, 20},  # 볼(써마지FLX · 울쎄라 · 소프웨이브 · 볼 필러 20)
    "C-BOLSAG": {20, 21},  # 볼처짐 — 써마지와 겹침 0
    "S-FACE": set(range(1, 40)),
    "E-TIGHT": {1, 2, 3, 30, 31},  # 타이트닝(써마지FLX · 티타늄 30 · 포트라 31) — 울쎄라 · 소프웨이브 밖
    "R-TIT": {30},
    "R-POT": {31},
    "S-GL": {40, 41, 42, 43},  # 미간
    "C-GL": {40, 41, 42, 43},  # 미간주름
    "P-BTX": {40, 41, 50},  # 보톡스(미간 40 · 41 · 턱 50)
    "M-THREAD": set(),  # 실리프팅 — 명동점 0
    "R-INMODE": set(),  # 인모드 — 명동점 0
    "S-JAW": {50, 51, 52},
    "S-JAWUNDER": {52, 53},  # 턱밑(턱의 자손)
    "C-DOUBLE": {52, 53},  # 이중턱
    "C-BOLSAL": {20, 22},  # 볼살
}


def V(code: str, name: str | None = None, **kw) -> dict:
    t = {"S": "site", "C": "concern", "P": "procedure", "R": "product", "E": "effect", "M": "modality"}[code[0]]
    return {"table": t, "code": code, "name": name or code, **kw}


async def toy(conds: dict) -> list[str]:
    sets = []
    for s in ("procedure", "site", "concern", "effect", "product"):
        if conds.get(s):
            got: set[int] = set()
            for x in conds[s]:
                got |= U.get(x["code"], set())
            sets.append(got)
    if not sets:
        return []
    inter = set.intersection(*sets)
    for x in conds.get("exclude") or []:
        inter -= U.get(x["code"], set())
    return [str(i) for i in sorted(inter)]


def step(topics, active, vals, n, *, text="", question=False, llm=None, exclude=()):
    turn, order = tp.turn_conditions(list(vals), [], None, exclude=[V(x) for x in exclude])
    return asyncio.run(
        tp.decide(topics, active, turn, order, n=n, llm=llm, withdrawn=[], count=toy, utterance=text, question=question)
    )


def codes(t: dict, slot: str) -> list[str]:
    return [v["code"] for v in t["conditions"].get(slot) or []]


def cur(d):
    return next(t for t in d.topics if t["id"] == d.active)


# ---------- §3-D 연속 재생 ----------


def test_s05_t1_to_t4_list_held_replace_then_restore():
    """S05 — 써마지 → 「볼이 처져서요」(0 → 앞 목록 + 까닭 · 볼 유지) → 「아니 써마지 말고 울쎄라로」(교체 · 볼 유지)
    → 「아니 아까 거로 다시」(1차 = 써마지 → 사진 복원 = T2)."""
    thm = [V("R-THM", "써마지"), V("P-RF", "고주파", **{"from": "product"})]
    d1 = step([], None, thm, 1, text="써마지 받고 싶어요")
    L1 = cur(d1)["items"]
    assert L1 == ["1", "2", "3", "4", "5"]
    d2 = step(d1.topics, d1.active, [V("C-BOLSAG", "볼처짐"), V("S-BOL", "볼")], 2, text="볼이 처져서요")
    t2 = cur(d2)
    assert d2.ls == "LS1" and t2["items"] == L1  # #10 앞 목록
    assert codes(t2, "site") == ["S-BOL"] and codes(t2, "product") == ["R-THM"]  # 조건 = kept(볼 유지 · D6)
    assert t2["list_held"] == {"slot": "concern", "values": ["볼처짐"]} and d2.rec["list_held"]
    ult = [V("R-ULT", "울쎄라"), V("P-HIFU", "HIFU", **{"from": "product"})]
    d3 = step(d2.topics, d2.active, ult, 3, text="아니 써마지 말고 울쎄라로 알려줘", llm={"move": "undo", "to": None})
    t3 = cur(d3)
    assert d3.rec["undo"]["skipped"] == "new_value"  # HU1 — 사진에 없는 울쎄라 = 교체
    assert codes(t3, "site") == ["S-BOL"] and codes(t3, "product") == ["R-ULT"]  # #11 볼 유지
    assert t3["items"] == ["10", "11"] and "list_held" not in t3
    d4 = step(d3.topics, d3.active, thm, 4, text="아니 아까 거로 다시 보여줘", llm={"move": "undo", "to": None})
    t4 = cur(d4)
    assert not d4.rec["undo"].get("skipped")  # 써마지 · 고주파 ⊆ 사진 → 복원
    assert t4["items"] == L1 and codes(t4, "site") == ["S-BOL"] and codes(t4, "product") == ["R-THM"]
    assert t4["list_held"]["slot"] == "concern"  # 사진의 표지도 돌아옴


def test_s06_t1_to_t3_suspend_then_widen():
    """S06 — 얼굴 타이트닝 → 「그중에서 소프웨이브 · 써마지 · 울쎄라만」(둘이 효과 밖 → 효과 쉼 · 셋 다) → 「다른 시술도」(첫 목록)."""
    d1 = step([], None, [V("E-TIGHT"), V("S-FACE")], 1)
    L1 = cur(d1)["items"]
    picks = [V("R-SOF", "소프웨이브"), V("R-THM", "써마지"), V("R-ULT", "울쎄라")]
    d2 = step(d1.topics, d1.active, picks, 2, text="그중에서 소프웨이브, 써마지, 울쎄라만 관심이 있어")
    t2 = cur(d2)
    assert codes(t2, "effect") == ["E-TIGHT"] and t2["conditions"]["suspended"] == ["effect"]  # 칸에 남김 · 쉼
    assert set(d2.rec["suspended"]["names"]) == {"소프웨이브", "울쎄라"}
    assert set(t2["items"]) == {"1", "2", "3", "4", "5", "10", "11", "12"}  # #13 셋 다
    d3 = step(d2.topics, d2.active, [], 3, text="다른 시술도 보여줘")
    t3 = cur(d3)
    assert t3["items"] == L1 and codes(t3, "effect") == ["E-TIGHT"] and "suspended" not in t3["conditions"]  # #14
    assert not codes(t3, "product")
    d4 = step(d3.topics, d3.active, [V("R-TIT", "티타늄리프팅"), V("R-POT", "포트라")], 4)
    assert cur(d4)["items"] == ["30", "31"] and "suspended" not in cur(d4)["conditions"]  # 효과 안 → 쉼 없음


def test_spelling_and_typo_statement_is_pick_not_question():
    """r2 P1-A — 맞춤법 꼴 「있어」 · 오타 꼴 「잇어」(「?」 없음 · 1차 kinds 빔) → 물음 아님 → 고르기 교체."""
    q_ok = Consult._is_question
    for text in ("그중에서 소프웨이브, 써마지, 울쎄라만 관심이 있어", "그중에서 소프웨이브, 써마지, 울쎄라만 관심이 잇어"):
        assert not q_ok(SimpleNamespace(question=text), SimpleNamespace(kinds=[]))
        d1 = step([], None, [V("E-TIGHT"), V("S-FACE")], 1)
        d2 = step(d1.topics, d1.active, [V("R-THM", "써마지")], 2, text=text, question=False)
        assert d2.rec["move"] == "deepen" and codes(cur(d2), "product") == ["R-THM"]
    assert q_ok(SimpleNamespace(question="미간주름에 보톡스만 가능해 ?"), SimpleNamespace(kinds=["indication"]))
    assert not q_ok(SimpleNamespace(question="미간주름에 보톡스만 가능해 ?"), SimpleNamespace(kinds=[]))  # 소넷이 정보 종류를 안 냄
    assert not q_ok(SimpleNamespace(question="보톡스 있어"), SimpleNamespace(kinds=["indication"]))  # 「?」 없음


def test_hq1_question_keeps_list_and_pick_exclude_go_to_material():
    """S10 T6 · S11 T6 꼴 — 물음 + 부위 · 고민 = 지금 주제 → 그대로 · 고르기 · 뺌은 답 재료로만(칸에 안 넣음 · r3 P2-4)."""
    d1 = step([], None, [V("C-GL"), V("S-GL")], 1)
    d2 = step(d1.topics, d1.active, [V("C-GL"), V("P-BTX", "보톡스")], 2, text="미간주름에 보톡스만 가능해 ?",
              question=True, exclude=["R-THM"])
    t2 = cur(d2)
    assert d2.rec["move"] == "same" and d2.rec["info"] and t2["items"] == cur(d1)["items"]
    assert not codes(t2, "procedure") and not t2["conditions"].get("exclude")
    assert d2.rec["asked_pick"] == {"pick": ["보톡스"], "exclude": ["R-THM"]}
    # 원함(「?」 없음)이면 고르기 교체(11-23 그대로)
    d3 = step(d1.topics, d1.active, [V("P-BTX", "보톡스")], 2, text="보톡스로 맞고 싶어서")
    assert codes(cur(d3), "procedure") == ["P-BTX"] and cur(d3)["items"] == ["40", "41"]


def test_hq2_out_pick_keeps_list_with_or_without_question_mark():
    """S09 T2 · T3 — 명동점 진료항목 0 인 고르기(실리프팅 · 인모드)는 말 꼴과 무관하게 적용 안 함 · 앞 목록 · out_pick."""
    d1 = step([], None, [V("E-TIGHT"), V("S-FACE")], 1)
    for text, q in (("그럼 실 리프팅은 없어 명동점에 ?", True), ("실 리프팅은 없어 명동점에", False)):
        d2 = step(d1.topics, d1.active, [V("M-THREAD", "실리프팅")], 2, text=text, question=q)
        assert d2.rec["out_pick"] == ["실리프팅"] and cur(d2)["items"] == cur(d1)["items"]
    d3 = step(d1.topics, d1.active, [V("R-INMODE", "인모드")], 2, text="인모드는 받을 수 잇어 ?", question=True)
    assert d3.rec["out_pick"] == ["인모드"] and cur(d3)["items"] == cur(d1)["items"]


def test_hu1_site_correction_still_undoes():
    """HU1 보강 — 새 값이 부위 · 고민이면 직전 변화가 다른 주제로 간 것 → 되돌린 뒤 적용(실물 67bb T4 · test_v05_topic_tuple 그대로)."""
    d1 = step([], None, [V("C-GL"), V("S-GL")], 1)
    d2 = step(d1.topics, d1.active, [V("P-BTX")], 2)
    d3 = step(d2.topics, d2.active, [V("S-JAW"), V("P-BTX")], 3, text="아니 턱에 보톡스", llm={"move": "undo", "to": None})
    assert not d3.rec["undo"].get("skipped") and codes(next(t for t in d3.topics if t["id"] == "t1"), "procedure") == []


def test_s10_t3_t4_new_topic_after_ha1_site():
    """S10 T3 · T4 — HA1 로 이번 턴 부위가 턱밑(이중턱 고민 부위 · 턱의 자손)이면 턱 주제와 부딪혀 새 주제(#17)."""
    d1 = step([], None, [V("S-JAW"), V("P-BTX")], 1)
    d2 = step(d1.topics, d1.active, [V("S-JAWUNDER", "턱밑", **{"from": "concern_site"}), V("C-DOUBLE", "이중턱")], 2,
              text="턱이 이중턱이라고")
    assert d2.rec["move"] == "new" and len(d2.topics) == 2 and cur(d2)["items"] == ["52", "53"]
    assert codes(next(t for t in d2.topics if t["id"] == "t1"), "procedure") == ["P-BTX"]  # 턱 보톡스 주제 그대로


def test_hs2_zero_pair_not_created():
    """HS2 — 찢은 짝 가운데 0 인 짝은 주제를 만들지 않고 blocked(split)로."""
    subs = [
        {"turn": tp.turn_conditions([V("S-GL"), V("C-GL")], [], None)[0], "site": "S-GL", "keep_concern": []},
        {"turn": tp.turn_conditions([V("S-BOL"), V("C-GL")], [], None)[0], "site": "S-BOL", "keep_concern": []},
    ]
    d = asyncio.run(tp.split_decide([], None, subs, n=1, count=toy))
    assert len(d.topics) == 1 and d.blocked["slot"] == "split" and d.blocked["sites"] == ["S-BOL"]


# ---------- HA1 · HX1(가짜 허브 · 가짜 매핑) ----------

SITES = {
    "S-FACE": {"name": "얼굴", "parent_code": "", "region": "face"},
    "S-JAW": {"name": "턱", "parent_code": "S-FACE", "region": "face"},
    "S-JAWUNDER": {"name": "턱밑", "parent_code": "S-JAW", "region": "face"},
    "S-BOL": {"name": "볼", "parent_code": "S-FACE", "region": "face"},
    "S-MOUTH": {"name": "입꼬리", "parent_code": "S-FACE", "region": "face"},
    "S-MARI": {"name": "마리오네트라인", "parent_code": "S-FACE", "region": "face"},
    "S-EYE": {"name": "눈 주변", "parent_code": "S-FACE", "region": "face"},
    "S-BODY": {"name": "전신", "parent_code": "", "region": "body"},
}


class FakeKos:
    sites = SITES
    item = {"A": {"hub": {"site_codes": ["S-FACE"]}}, "B": {"hub": {"site_codes": ["S-BODY"]}}}

    def ancestors(self, s: str) -> list[str]:
        out, c = [], SITES.get(s, {}).get("parent_code")
        while c:
            out.append(c)
            c = SITES.get(c, {}).get("parent_code")
        return out


class FakeDet:
    CS = {"C-DOUBLE": ["S-JAWUNDER"], "C-BOLSAL": ["S-BOL"], "C-MARI": ["S-MOUTH", "S-MARI"], "C-GEN": [],
          "C-EYEW": ["S-EYE"], "C-MOUTHW": ["S-MOUTH"], "C-AGING": ["S-FACE"]}

    async def concern(self, code, res):  # 일반 고민 「주름」 은 허브 행에 하위 · 상위 고민이 섞여 옴
        if code == "C-GEN":
            res.upper.extend(["C-EYEW", "C-MOUTHW", "C-AGING"])
        return set()

    def concern_sites(self, code):
        return list(self.CS.get(code, []))


def ha1(targets):
    return asyncio.run(Consult._concern_site_targets(SimpleNamespace(), FakeKos(), FakeDet(), targets))


def test_ha1_deterministic_sites():
    out, rec = ha1([V("S-JAW", "턱"), V("C-DOUBLE", "이중턱")])  # 자손 → 바꿈
    assert [t["code"] for t in out if t["table"] == "site"] == ["S-JAWUNDER"] and rec["replaced"]
    out, _ = ha1([V("C-DOUBLE", "이중턱")])  # 1차 부위 없음(r3) → 고민 부위
    assert [t["code"] for t in out if t["table"] == "site"] == ["S-JAWUNDER"]
    out, _ = ha1([V("S-BOL", "볼"), V("C-BOLSAL")])  # 같음 → 그대로
    assert [t["code"] for t in out if t["table"] == "site"] == ["S-BOL"]
    out, _ = ha1([V("S-JAWUNDER"), V("C-GEN")])  # 일반 고민 → 바꿈 없음
    assert [t["code"] for t in out if t["table"] == "site"] == ["S-JAWUNDER"]
    out, rec = ha1([V("C-MARI", "마리오네트라인")])  # 고민 부위 둘 · 1차 부위 없음 → 부위 안 넣음
    assert not [t for t in out if t["table"] == "site"] and rec["multi"][0]["sites"] == ["S-MOUTH", "S-MARI"]


def test_hx1_site_axis_first():
    k = FakeKos()
    a = ax.site_first(k, ["A", "B"])
    assert a["axis"] == "site" and {o["value"] for o in a["options"]} == {"S-FACE", "S-BODY"}
    assert ax.site_first(k, ["A"]) is None
    m = ax.site_first(k, ["A"], multi=[{"concern": "C-MARI", "sites": ["S-MOUTH", "S-MARI"]}])
    assert [o["value"] for o in m["options"]] == ["S-MOUTH", "S-MARI"]


def chain(topics, active, targets, n, text=""):
    """1차 대상 → HA1(가짜 허브 고민 부위) → 이번 턴 조건 → 주제 판단(검수 P2-1 — 실제 차례 그대로)."""
    out, _ = ha1(targets)
    turn, order = tp.turn_conditions(out, [], None)
    return asyncio.run(tp.decide(topics, active, turn, order, n=n, llm=None, withdrawn=[], count=toy, utterance=text))


def test_s10_t3_t4_chain_through_ha1_both_forms():
    """§3-D S10 T3 · T4 연속 — 1차가 부위를 냈든(r1 · r2) 안 냈든(r3) 같은 결과(새 주제 둘)."""
    for t3, t4 in (([V("S-JAW", "턱"), V("C-DOUBLE", "이중턱")], [V("S-BOL", "볼"), V("C-BOLSAL", "볼살")]),
                   ([V("C-DOUBLE", "이중턱")], [V("C-BOLSAL", "볼살")])):
        d1 = chain([], None, [V("S-JAW", "턱"), V("P-BTX", "보톡스")], 1, text="턱에 보톡스 맞아도돼 ?")
        d2 = chain(d1.topics, d1.active, [V("S-JAW", "턱"), V("P-BTX", "보톡스")], 2, text="턱 보톡스 맞고 싶다고")
        assert len(d2.topics) == 1 and cur(d2)["items"] == ["50"]
        d3 = chain(d2.topics, d2.active, t3, 3, text="턱이 이중턱이라고")
        assert d3.rec["move"] == "new" and codes(cur(d3), "site") == ["S-JAWUNDER"] and cur(d3)["items"] == ["52", "53"]
        assert codes(next(t for t in d3.topics if t["id"] == "t1"), "procedure") == ["P-BTX"]  # 턱 보톡스 그대로(#17)
        d4 = chain(d3.topics, d3.active, t4, 4, text="볼살도 너무 많아")
        assert d4.rec["move"] == "new" and len(d4.topics) == 3 and codes(cur(d4), "site") == ["S-BOL"]
        assert cur(d4)["items"] == ["20"]
    # (대조) HA1 없이 고민만 오면 턱 보톡스 주제의 빈 고민 칸을 채워 깊게 간다 — 고친 구멍(H14b)
    turn, order = tp.turn_conditions([V("C-DOUBLE")], [], None)
    raw = asyncio.run(tp.decide(d2.topics, d2.active, turn, order, n=3, llm=None, withdrawn=[], count=toy))
    assert raw.rec["move"] == "deepen"


def test_hs1_general_concern_to_sub_concern_pairs():
    """검수 P2-3 — 「눈가 · 입꼬리 + 주름(일반)」 → 허브 행에 하위(눈가주름 · 입꼬리주름) · 상위(노화 — 얼굴) 고민이 섞여 와도
    부위마다 하위 고민만 붙어 주제 둘로 찢는다(S03 T2 r2 꼴)."""

    async def noop(*a, **k):
        return None

    me = SimpleNamespace(_cur_topic=lambda: None, el=SimpleNamespace(name=lambda t, c: c), _product_procedures=noop)
    targets = [V("S-EYE", "눈 주변"), V("S-MOUTH", "입꼬리"), V("C-GEN", "주름")]
    acc = SimpleNamespace(targets=targets, pairs=[], country=None, exclude=[])
    subs, rec = asyncio.run(Consult._split_subs(me, FakeKos(), FakeDet(), acc, targets))
    assert subs is not None and [s["site"] for s in subs] == ["S-EYE", "S-MOUTH"]
    assert [[v["code"] for v in s["turn"]["concern"]] for s in subs] == [["C-EYEW"], ["C-MOUTHW"]]  # 상위 C-AGING 안 붙음
    assert rec["from"] == "rule"
    # 부위 둘 + 부위 없는 고민이 하위 고민도 못 찾으면 찢지 않음
    t2 = [V("S-EYE"), V("S-MOUTH"), V("C-NONE", "모름")]
    subs2, _ = asyncio.run(Consult._split_subs(me, FakeKos(), FakeDet(), SimpleNamespace(targets=t2, pairs=[], country=None,
                                                                                         exclude=[]), t2))
    assert subs2 is None
