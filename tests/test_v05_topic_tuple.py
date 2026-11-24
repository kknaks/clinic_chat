"""주제 = 칸 묶음(튜플) — 사용자 결정 11-22(옛 TP1 ~ TP14 판단 대체). 외부 호출 0(장난감 셈 · 가짜 CLI · 가짜 허브).

판단(위에서 처음 맞는 것): 칸 0 · 정보 질문 → 그대로 / 부딪힘 없음 → 깊게 / 앞 주제 하나와 맞음 → 되돌림 /
둘 이상 → LLM `to` · 없으면 물음 / 어느 것과도 부딪힘 → 새 주제. 부딪힘 칸 = 부위 · 고민 · 시술.
실물 a7d7e253 꼴 10 턴을 가짜 이해 결과로 한 줄씩.
"""

from __future__ import annotations

import asyncio
import json

from clinic_chat.consult import kos_block as kb
from clinic_chat.consult import topics as tp
from clinic_chat.consult.detect import NameHit
from tests.state_fixture import Run, state_hub
from tests.test_v05_detect import EXTRA_ENTRY
from tests.v04_fixture import entry_rows, list_route

U = {
    "C-NL": set(range(1, 45)),  # 팔자주름 고민 44
    "S-NL": set(range(1, 45)),  # 팔자주름 부위
    "S-GL": set(range(35, 41)) | set(range(100, 116)) | set(range(200, 206)),  # 미간
    "C-GL": set(range(35, 41)) | set(range(100, 116)) | set(range(200, 206)),  # 미간주름
    "P-BTX": set(range(30, 41)) | set(range(100, 121)),  # 보톡스
    "P-FIL": set(range(1, 10)),
    "R-XEO": {100, 101, 102, 36},  # 제오민
    "R-HTS": {103, 104, 37},  # 하이톡스
    "S-JAW": {300, 301},
    "C-JAW": {300, 301, 302},
    "R-NONE": set(),
    "S-NONE": set(),
    "P-RF": set(range(100, 111)) | {20},  # 고주파(써마지 시술)
    "S-FORE": set(range(100, 105)) | {300},  # 이마
    "R-THM": set(range(100, 105)),  # 써마지(제품)
    "S-BOL": set(range(1, 10)) | {400},
    "C-VOL": set(range(1, 10)),
    "C-GIMI": set(range(400, 420)),
}


def V(code: str) -> dict:
    t = {"S": "site", "C": "concern", "P": "procedure", "R": "product"}[code[0]]
    return {"table": t, "code": code, "name": code}


async def toy(conds: dict) -> list[str]:
    sets = []
    for s in ("procedure", "site", "concern", "effect", "product"):
        if conds.get(s):
            got: set[int] = set()
            for x in conds[s]:
                got |= {int(i) for i in x["items"]} if x.get("table") == "name" else U.get(x["code"], set())
            sets.append(got)
    if not sets:
        return []
    inter = set.intersection(*sets)
    for x in conds.get("exclude") or []:
        inter -= U.get(x["code"], set())
    return [str(i) for i in sorted(inter)]


def step(topics, active, vals, n, *, llm=None, exclude=(), hits=(), info=False, max_n=None, text=""):
    turn, order = tp.turn_conditions(list(vals), list(hits), None, exclude=[V(x) for x in exclude])
    return asyncio.run(
        tp.decide(
            topics, active, turn, order, n=n, llm=llm, withdrawn=[], count=toy, info=info, max_n=max_n, utterance=text
        )
    )


def codes(t: dict, slot: str) -> list[str]:
    return [v["code"] for v in t["conditions"].get(slot) or []]


def by_id(d, tid):
    return next(t for t in d.topics if t["id"] == tid)


# ---------- 규칙 4 각 경우 ----------


def test_empty_turn_keeps_topic():
    d1 = step([], None, [V("C-NL")], 1)
    d2 = step(d1.topics, d1.active, [], 2, llm={"move": "new", "to": None})
    assert d2.active == "t1" and d2.topics == d1.topics and d2.rec["move"] == "same" and d2.rec["llm_agree"] is False


def test_deepen_fills_empty_slot_and_replaces_narrow():
    d1 = step([], None, [V("S-GL"), V("C-GL")], 1)
    d2 = step(d1.topics, d1.active, [V("P-BTX")], 2)
    assert d2.rec["move"] == "deepen" and codes(d2.topics[0], "procedure") == ["P-BTX"]
    d3 = step(d2.topics, d2.active, [V("R-XEO")], 3)
    d4 = step(d3.topics, d3.active, [V("R-HTS")], 4)  # 제품 = 좁힘 칸 → 교체(새 주제 아님)
    assert (
        len(d4.topics) == 1
        and codes(d4.topics[0], "product") == ["R-HTS"]
        and d4.topics[0]["items"] == ["37", "103", "104"]
    )


def test_conflict_new_topic_then_return_one_fit():
    d1 = step([], None, [V("C-NL"), V("S-NL")], 1)
    d2 = step(d1.topics, d1.active, [V("S-GL"), V("C-GL")], 2)
    assert d2.active == "t2" and d2.rec["move"] == "new" and d2.rec["conflict"] == ["site", "concern"]
    assert codes(d2.topics[1], "site") == ["S-GL"] and not codes(d2.topics[1], "procedure")  # 이번 턴 칸만
    d3 = step(d2.topics, d2.active, [V("C-NL")], 3)
    assert d3.active == "t1" and d3.rec["move"] == "return" and d3.rec["to_from"] == "rule"
    assert len(d3.topics[0]["items"]) == 44  # 저장 상태 그대로


def test_two_fits_llm_to_or_ask():
    d1 = step([], None, [V("C-NL")], 1)  # t1 고민 팔자주름
    d2 = step(d1.topics, d1.active, [V("C-GL")], 2)  # t2 고민 미간주름
    d3 = step(d2.topics, d2.active, [V("C-JAW")], 3)  # t3 고민 턱(지금)
    fit_both = [V("P-BTX")]  # 시술만 — t3 에 깊게 가능 → 깊게(부딪힘 없음이 먼저)
    assert step(d3.topics, d3.active, fit_both, 4).rec["move"] == "deepen"
    # 지금 주제와 부딪히고(부위 턱 vs 미간) 앞 주제 둘(t1 · t2)과 맞음 — 둘 다 부위 칸 빔
    d3b = step(d3.topics, d3.active, [V("S-JAW")], 4)
    assert d3b.rec["move"] == "deepen"  # t3 은 부위 칸 빔 → 깊게
    d4 = step(d3b.topics, d3b.active, [V("S-GL")], 5)
    assert d4.rec["move"] == "ask" and d4.ask == ["t2", "t1"] and d4.active == "t3" and d4.topics == d3b.topics
    d5 = step(d3b.topics, d3b.active, [V("S-GL")], 5, llm={"move": "return", "to": "t2"})
    assert d5.active == "t2" and d5.rec["to_from"] == "llm" and codes(d5.topics[1], "site") == ["S-GL"]
    d6 = step(d3b.topics, d3b.active, [V("S-GL")], 5, llm={"move": "return", "to": "t9"})  # 무효 to → 물음
    assert d6.rec["move"] == "ask"


def test_info_question_keeps_conditions():
    d1 = step([], None, [V("C-NL")], 1)
    hit = NameHit("BNM", "kos_procedure", ["5"], 0, 3)
    d2 = step(d1.topics, d1.active, [], 2, hits=[hit], info=True)
    assert d2.rec["move"] == "same" and d2.rec["info"] and d2.topics == d1.topics
    d3 = step(d1.topics, d1.active, [], 2, hits=[hit], info=False)  # 정보 질문 아님 → 깊게(이름 일치 = 시술 칸)
    assert d3.rec["move"] == "deepen" and d3.topics[0]["items"] == ["5"]


def test_exclude_narrows_and_is_not_procedure():
    d1 = step([], None, [V("C-NL"), V("S-NL")], 1)
    d2 = step(d1.topics, d1.active, [V("C-NL")], 2, exclude=["P-BTX"])
    t = d2.topics[0]
    assert (
        d2.rec["move"] == "deepen"
        and not codes(t, "procedure")
        and [v["code"] for v in t["conditions"]["exclude"]] == ["P-BTX"]
    )
    assert len(t["items"]) == 44 - 11 and "30" not in t["items"]
    d3 = step(d2.topics, d2.active, [V("P-BTX")], 3)  # 다시 말하면 뺌에서 지움
    assert not d3.topics[0]["conditions"]["exclude"] and codes(d3.topics[0], "procedure") == ["P-BTX"]


def test_deepen_zero_blocks_last_slot():
    d1 = step([], None, [V("C-NL")], 1)
    d2 = step(d1.topics, d1.active, [V("S-NONE")], 2)  # 부위 채움으로 0 — 막음(고르기 칸 아님)
    assert d2.ls == "LS1" and d2.blocked["slot"] == "site" and len(d2.topics[0]["items"]) == 44


def test_1123_pick_replace_zero_follows():
    """11-23 — 고르기(시술 · 제품) 교체로 0 이면 막지 않고 따른다(앞 목록으로 묶지 않음 · 봇이 0 이라고 말함)."""
    d1 = step([], None, [V("C-NL"), V("P-FIL")], 1)  # 팔자주름 · 필러(1 ~ 9)
    # SPEC-012 HQ2 — 명동점 진료항목이 있는 고르기(써마지 100 ~ 104)가 지금 고민과 겹쳐 0 → 따른다(11-23 그대로)
    d2 = step(d1.topics, d1.active, [V("R-THM")], 2)
    t = d2.topics[0]
    assert d2.rec["pick_zero"] and d2.ls is None and not d2.blocked and t["items"] == []
    assert codes(t, "product") == ["R-THM"] and not codes(t, "procedure")  # 둘 다 교체
    # 명동점 진료항목 0 인 고르기(R-NONE = 밖 · 없는 시술)는 HQ2 → 적용 안 함 · 앞 목록 그대로 · out_pick
    d3 = step(d1.topics, d1.active, [V("R-NONE")], 2)
    assert d3.rec["out_pick"] == ["R-NONE"] and d3.topics[0]["items"] == d1.topics[0]["items"]
    assert codes(d3.topics[0], "procedure") == ["P-FIL"]


def test_conflict_ignores_name_hits_and_same_name_other_table():
    """실물 9 턴 — 「아까 미간 보톡스는 제오민이 나아?」 = 계열 M1 「보톡스」 + 이름 일치 「미간보톡스」 · ② 는 시술 PRC-001 「보톡스」."""
    t2 = {"site": [V("S-GL")], "procedure": [{"table": "procedure", "code": "PRC-001", "name": "보톡스"}]}
    turn, _ = tp.turn_conditions(
        [V("S-GL"), {"table": "modality", "code": "M1", "name": "보톡스"}],
        [NameHit("미간보톡스", "kos_procedure", ["1"], 0, 5)],
        None,
    )
    assert tp.conflicts(turn, t2) == []
    other = {"table": "modality", "code": "M2", "name": "필러"}
    assert tp.conflicts(tp.turn_conditions([other], [], None)[0], t2) == []  # 11-23 — 시술은 부딪힘 칸 아님(교체)
    assert tp.conflicts(tp.turn_conditions([V("S-JAW")], [], None)[0], t2) == ["site"]


# ---------- O-14 — 같은 값 앞 주제가 깊게보다 먼저(사용자 결정 11-22) ----------


def test_o14_same_values_prev_topic_wins_over_deepen():
    d1 = step([], None, [V("S-BOL"), V("C-VOL"), V("P-FIL")], 1)  # ① 볼 · 볼륨 손실 · 필러
    d2 = step(d1.topics, d1.active, [V("C-GIMI")], 2)  # ② 기미(부딪힘 → 새 주제)
    assert d2.active == "t2" and not codes(d2.topics[1], "site") and not codes(d2.topics[1], "procedure")
    d3 = step(d2.topics, d2.active, [V("S-BOL"), V("P-FIL")], 3)  # 「아까 볼 필러 다시」 — ② 빈 칸 채움(깊게) 아님
    assert d3.active == "t1" and d3.rec["move"] == "return" and d3.rec["same_values"] and d3.rec["to_from"] == "rule"
    assert d3.topics[1]["conditions"] == d2.topics[1]["conditions"]  # ② 그대로(볼 · 필러가 섞이지 않음)
    assert len(d3.topics[0]["items"]) == 9  # ① 저장 상태


def test_o14_no_prev_with_value_deepens_current():
    d1 = step([], None, [V("C-NL"), V("S-NL")], 1)  # ① 팔자주름
    d2 = step(d1.topics, d1.active, [V("S-GL"), V("C-GL")], 2)  # ② 미간
    d3 = step(d2.topics, d2.active, [V("P-BTX")], 3)  # 「보톡스로」 — 앞 주제에 보톡스 없음 → 미간 깊게
    assert d3.active == "t2" and d3.rec["move"] == "deepen" and not d3.rec["same_values"]
    d4 = step(d3.topics, d3.active, [V("S-GL"), V("P-BTX")], 4)  # 지금 주제가 이미 가짐 → 머무름(깊게)
    assert d4.active == "t2" and d4.rec["move"] == "deepen"


def test_o14_two_prev_with_value_llm_or_ask():
    d1 = step([], None, [V("S-BOL"), V("C-VOL")], 1)  # ① 볼 · 볼륨 손실
    d2 = step(d1.topics, d1.active, [V("S-BOL"), V("C-GIMI")], 2)  # ② 볼 · 기미
    d3 = step(d2.topics, d2.active, [V("C-JAW")], 3)  # ③ 턱(지금)
    assert d3.active == "t3" and len(d3.topics) == 3
    d4 = step(d3.topics, d3.active, [V("S-BOL")], 4)  # 볼 — ① · ② 둘 다 같은 값
    assert d4.rec["move"] == "ask" and set(d4.ask) == {"t1", "t2"} and d4.rec["same_values"]
    d5 = step(d3.topics, d3.active, [V("S-BOL")], 4, llm={"move": "return", "to": "t1"})
    assert d5.active == "t1" and d5.rec["to_from"] == "llm"


def test_o15_info_question_includes_price_words():
    assert tp.info_question(["overview"], False, "볼뉴머가 머야")
    assert tp.info_question([], False, "볼뉴머 가격은 얼마예요?") and tp.info_question([], False, "비용이 궁금해")
    assert not tp.info_question([], False, "보톡스로 맞고 싶어서")
    d1 = step([], None, [V("C-NL")], 1)
    hit = NameHit("BNM", "kos_procedure", ["5"], 0, 3)
    d2 = step(d1.topics, d1.active, [], 2, hits=[hit], info=tp.info_question([], False, "볼뉴머 가격은?"))
    assert d2.rec["move"] == "same" and d2.topics == d1.topics  # 목록 안 것 가격 = 주제 그대로


def test_o14_spec031_current_holds_same_value_stays():
    """스펙 0.3.1 확정 (1) — 지금 주제에도 같은 값이 있으면 되돌리지 않고 지금 주제를 깊게."""
    d1 = step([], None, [V("C-NL"), V("P-BTX")], 1)  # ① 팔자주름 · 보톡스
    d2 = step(d1.topics, d1.active, [V("C-GL"), V("P-BTX")], 2)  # ② 미간주름 · 보톡스(지금)
    d3 = step(d2.topics, d2.active, [V("P-BTX"), V("R-XEO")], 3)  # 보톡스 + 제오민 — ① 도 보톡스를 가짐
    assert d3.active == "t2" and d3.rec["move"] == "deepen" and not d3.rec["same_values"]


def test_o14_spec031_narrow_only_turn_never_returns():
    """스펙 0.3.1 확정 (2) — 이번 턴이 좁힘 칸(제품 · 효과 · 국가)만이면 같은 값 되돌림을 쓰지 않는다."""
    d1 = step([], None, [V("C-GL"), V("P-BTX"), V("R-XEO")], 1)  # ① 미간주름 · 보톡스 · 제오민
    d2 = step(d1.topics, d1.active, [V("C-NL")], 2)  # ② 팔자주름(지금)
    d3 = step(d2.topics, d2.active, [V("R-XEO")], 3)  # 제오민만 — ① 이 같은 값을 가져도 되돌림 아님
    assert d3.active == "t2" and d3.rec["move"] == "deepen" and not d3.rec["same_values"]
    assert not tp.holds(tp.turn_conditions([V("R-XEO")], [], "KR")[0], d1.topics[0]["conditions"])


# ---------- 실물 a7d7e253 꼴 10 턴(가짜 이해 결과) ----------

SCENARIO = [
    ("팔자 주름이 고민이야", dict(vals=[V("C-NL"), V("S-NL")]), ("t1", "first")),
    ("미간에 주름도 너무 깊어", dict(vals=[V("S-GL"), V("C-GL")], llm={"move": "new", "to": None}), ("t2", "new")),
    ("보톡스로 맞고 싶어서", dict(vals=[V("P-BTX")], llm={"move": "continue", "to": None}), ("t2", "deepen")),
    ("아니 팔자주름 말고 미간에 보톡스 얘기한건데", dict(vals=[V("S-GL"), V("P-BTX")]), ("t2", "deepen")),
    (
        "아니 팔자주름 시술 추천은 그대로 두라니까",
        dict(vals=[V("C-NL")], llm={"move": "return", "to": "t1"}),
        ("t1", "return"),
    ),
    ("팔자주름에 보톡스 말고 다른 시술은 없어서 그대로 둔거지 ?", dict(vals=[V("C-NL")], info=True), ("t1", "deepen")),
    ("팔자주름에 보톡스 말고 다른 시술 알려달라니까", dict(vals=[V("C-NL")], exclude=["P-BTX"]), ("t1", "deepen")),
    ("볼뉴머가 머야", dict(vals=[], hits=[NameHit("BNM", "kos_procedure", ["5"], 0, 3)], info=True), ("t1", "same")),
    ("아까 미간 보톡스는 제오민이 나아?", dict(vals=[V("S-GL"), V("P-BTX"), V("R-XEO")], info=True), ("t2", "return")),
    # 11-23 — 제품은 그 시술로도 칸을 채운다(turn.py `_product_procedures` — 여기서는 보톡스를 함께 넣어 흉내)
    ("제오민 말고 하이톡스", dict(vals=[V("P-BTX"), V("R-HTS")], exclude=["R-XEO"]), ("t2", "deepen")),
]


def test_scenario_a7d7e253_ten_turns():
    topics, active, seen = [], None, []
    for n, (_, kw, (want_active, want_move)) in enumerate(SCENARIO, 1):
        d = step(
            topics,
            active,
            kw["vals"],
            n,
            llm=kw.get("llm"),
            exclude=kw.get("exclude", ()),
            hits=kw.get("hits", ()),
            info=kw.get("info", False),
        )
        topics, active = d.topics, d.active
        t1 = by_id(d, "t1")
        seen.append(
            (n, active, d.rec["move"], len(t1["items"]), len(by_id(d, "t2")["items"]) if len(topics) > 1 else None)
        )
        assert (active, d.rec["move"]) == (want_active, want_move), (n, seen[-1])
        assert not codes(t1, "procedure"), n  # 미간 「보톡스」 가 ① 에 안 섞임
    t1, t2 = by_id(d, "t1"), by_id(d, "t2")
    assert (codes(t1, "site"), codes(t1, "concern")) == (["S-NL"], ["C-NL"])
    assert (codes(t2, "site"), codes(t2, "concern"), codes(t2, "procedure")) == (["S-GL"], ["C-GL"], ["P-BTX"])
    assert codes(t2, "product") == ["R-HTS"] and t2["items"] == ["37", "103", "104"]  # 10 턴 — 같은 주제 제품 교체
    assert [x[3] for x in seen] == [44, 44, 44, 44, 44, 44, 33, 33, 33, 33]  # ① 은 5 턴 44 · 7 턴 보톡스 뺌 33
    assert seen[8][4] == 4  # 9 턴 ② 되돌림 + 제오민(미간 ∩ 보톡스 ∩ 제오민 = 36 · 100 · 101 · 102)


def test_cond_view_ask_and_slots():
    d1 = step([], None, [V("C-NL")], 1)
    d2 = step(d1.topics, d1.active, [V("C-GL")], 2)
    d3 = step(d2.topics, d2.active, [V("C-JAW"), V("S-JAW")], 3)
    d4 = step(d3.topics, d3.active, [V("S-GL")], 4)
    from clinic_chat.consult import items_view as iv

    brief = [{"id": t["id"], "slots": iv.cond_tuple(t["conditions"]), "n": len(t["items"])} for t in d4.topics[:2]]
    ir = {"codes": d4.topics[2]["items"], "conditions": d4.topics[2]["conditions"], "ask_topics": d4.ask}
    cv = kb.cond_view(d4, ir, None, brief)
    assert cv["topic_move"] == "ask" and [x["id"] for x in cv["ask_topic"]] == ["t2", "t1"]
    assert cv["ask_topic"][0]["slots"] == {"concern": ["C-GL"]} and cv["slots"] == {
        "site": ["S-JAW"],
        "concern": ["C-JAW"],
    }


# ---------- 한 턴 끝까지 — 뺌(exclude) · 장부 ----------


def understand_raw(targets, *, exclude=(), kinds=(), topic=None) -> str:
    raw = json.dumps(
        {
            "targets": [{"table": t, "code": c, "sure": True} for t, c in targets],
            "kind": "new",
            "sites": [],
            "withdraw": [],
            "exclude": list(exclude),
            "kinds": list(kinds),
            "out": False,
            "country": None,
            "topic": topic,
        },
        ensure_ascii=False,
    )
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": raw,
            "duration_api_ms": 900,
            "usage": {"input_tokens": 3, "output_tokens": 20},
            "modelUsage": {"claude-test": {}},
        },
        ensure_ascii=False,
    )


def test_turn_exclude_narrows_topic_and_ledger(tmp_path, fake_cli):
    extra = [
        {
            "entity_table": "product",
            "entity_code": "ZPRD-002",
            "entity_name": "가짜톡스둘",
            "name_kind": "정식",
            "text": "가짜톡스둘",
        }
    ]
    hub = state_hub()
    hub.routes["resolve"] = list_route(entry_rows() + EXTRA_ENTRY + extra)
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        (fake_cli[1] / "understand.json").write_text(understand_raw([("procedure", "ZPRC-001")]), encoding="utf-8")
        row1, _, _ = r.turn("가짜보톡스시술")
        assert row1["items"]["codes"] == ["ZZ-BTX-A", "ZZ-BTX-B"]
        (fake_cli[1] / "understand.json").write_text(
            understand_raw([("product", "ZPRD-002")], exclude=["product:ZPRD-002"]), encoding="utf-8"
        )
        row2, _, _ = r.turn("가짜톡스둘은 빼 주세요")  # 11-23 — 「~ 다른 거」 는 넓히기 표지라 뺌만 묻는 말로
        u = row2["consult"]["understand"]["accepted"]
        assert u["exclude"] == [{"table": "product", "code": "ZPRD-002", "name": "가짜톡스둘"}] and u["targets"] == []
        t = row2["consult"]["topics"][0]
        assert row2["consult"]["active_topic"] == "t1" and row2["items"]["codes"] == ["ZZ-BTX-A"]
        assert row2["items"]["excluded"] == 1 and t["conditions"]["exclude"][0]["code"] == "ZPRD-002"
        assert row2["consult"]["topic"]["move"] == "deepen" and not t["conditions"]["product"]
        log = [json.loads(x) for x in r.log.read_text(encoding="utf-8").splitlines()]
        und = [x for x in log if "--no-session-persistence" in x["argv"]]
        assert "지금 주제: t1 (시술=가짜보톡스시술, 가짜 보톡스) (2)" in (
            und[-1].get("stdin") or ""
        )  # 이해 호출 = 칸 묶음 + 수
    finally:
        r.close()


# ---------- 11-23 넓히기 · 바로잡기 · 고르기 교체(사용자 「이거 고쳐봐」 · conv 67bb9faf) ----------

THERM = [V("P-RF"), V("R-THM")]  # 「써마지」 = 제품 + 그 시술(turn.py `_product_procedures` 흉내)
S67 = [
    ("팔자주름 고민이야", [V("C-NL"), V("S-NL")], {}, ("t1", "first")),
    ("미간 주름도 고민이야", [V("S-GL"), V("C-GL")], {}, ("t2", "new")),
    ("써마지 시술을 받고 싶어", THERM, {}, ("t2", "deepen")),
    ("아니 이마에 써마지를 받고 싶다고", [V("S-FORE"), *THERM], {"llm": {"move": "new", "to": None}}, ("t3", "new")),
    ("팔자주름은 시술 머머 잇다고 햇지 ?", [V("C-NL"), V("S-NL")], {"info": True}, ("t1", "return")),
    ("응 팔자주름에는 보톡스에 대해 알려줘", [V("C-NL"), V("S-NL"), V("P-BTX")], {"info": True}, ("t1", "deepen")),
    ("응 명동점 시술 뭐뭐 잇냐고", [], {"info": True}, ("t1", "same")),
]


def test_1123_conv67bb9faf_seven_turns():
    topics, active, log = [], None, []
    for n, (text, vals, kw, want) in enumerate(S67, 1):
        d = step(topics, active, vals, n, text=text, **kw)
        topics, active = d.topics, d.active
        log.append((n, active, d.rec["move"], {t["id"]: len(t["items"]) for t in topics}, d.rec["undo"]))
        assert (active, d.rec["move"]) == want, log[-1]
    t1 = by_id(d, "t1")
    assert codes(by_id(d, "t2"), "product") == [] and codes(by_id(d, "t3"), "product") == ["R-THM"]
    assert log[2][3]["t2"] == 5  # 3 턴 — 지금 주제 ② 미간 · 써마지(① 아님)
    assert log[3][4] == {
        "from": "rule",
        "marker": "아니",
        "topic": "t2",
        "removed": False,
        "restored": True,  # SPEC-012 HU1 기록 칸(새 값이 부위 → 되돌림)
    }  # 4 턴 바로잡기 = ② 써마지 뺌
    assert log[4][3]["t1"] == 44  # 5 턴 ① 되돌림 — 써마지가 들어간 적 없음
    assert (
        codes(t1, "procedure") == ["P-BTX"] and not codes(t1, "product") and len(t1["items"]) == 11
    )  # 6 턴 보톡스 > 0
    assert log[6][3] == log[5][3]  # 7 턴 그대로


def test_1123_undo_removes_topic_made_last_turn_and_llm_undo():
    d1 = step([], None, [V("C-NL")], 1)
    d2 = step(d1.topics, d1.active, [V("C-GL")], 2)  # 새 주제 ②(잘못 알아들음)
    d3 = step(d2.topics, d2.active, [V("C-JAW")], 3, text="그게 아니라 턱이요")
    assert d3.rec["undo"]["removed"] and [t["id"] for t in d3.topics] == ["t1", "t2"]
    assert codes(by_id(d3, "t2"), "concern") == ["C-JAW"]  # ② 지우고 턱으로 새 주제
    d4 = step(d1.topics, d1.active, [V("P-BTX")], 2)
    d5 = step(d4.topics, d4.active, [V("P-FIL")], 3, llm={"move": "undo", "to": None})  # 표지 없이 LLM undo
    assert d5.rec["undo"]["from"] == "llm" and codes(d5.topics[0], "procedure") == ["P-FIL"]
    assert d5.rec["llm_agree"] is None
    d6 = step(d4.topics, d4.active, [], 4, text="아니 그건 됐고")  # 직전(3) 턴에 바뀐 주제 없음 → 되돌릴 것 0
    assert d6.rec["undo"]["topic"] is None and codes(d6.topics[0], "procedure") == ["P-BTX"]


def test_1123_widen_clears_narrow_slots_keeps_site_concern():
    d1 = step([], None, [V("C-NL"), V("S-NL"), V("P-BTX")], 1, exclude=["R-XEO"])
    assert len(d1.topics[0]["items"]) == 10  # 팔자주름 ∩ 보톡스 11 - 제오민 뺌(36) 1
    d2 = step(d1.topics, d1.active, [], 2, text="다른 시술도 보여줘")
    t = d2.topics[0]
    assert d2.rec["widen"]["marker"] == "다른시술" and set(d2.rec["widen"]["cleared"]) == {"procedure", "exclude"}
    assert codes(t, "concern") == ["C-NL"] and codes(t, "site") == ["S-NL"] and not codes(t, "procedure")
    assert len(t["items"]) == 44 and d2.changed
    d3 = step(d1.topics, d1.active, [], 2, llm={"move": "widen", "to": None})
    assert d3.rec["widen"]["from"] == "llm" and len(d3.topics[0]["items"]) == 44


def test_1123_markers():
    assert tp.undo_marker("아니 이마에 써마지") == "아니" and tp.undo_marker("아니요 괜찮아요") is None
    assert tp.undo_marker("보톡스 말고 필러", has_exclude=False) == "말고"
    assert tp.undo_marker("보톡스 말고 다른 시술", has_exclude=True) is None  # 뺌이면 바로잡기 아님
    assert tp.widen_marker("시술 상관없이 다 보여줘") == "상관없이" and tp.widen_marker("보톡스로 맞고 싶어") is None


def test_1123_head_click_does_not_steer_next_turn_and_product_fills_procedure(tmp_path, fake_cli):
    from tests.test_v05_fix_a import SITE_ENTRY

    hub = state_hub()
    hub.routes["resolve"] = list_route(entry_rows() + EXTRA_ENTRY + SITE_ENTRY)
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        for text, tg in (("가짜턱", [("site", "ZS-072")]), ("가짜입", [("site", "ZS-057")])):
            (fake_cli[1] / "understand.json").write_text(understand_raw(tg), encoding="utf-8")
            row, _, _ = r.turn(text)
        assert row["consult"]["active_topic"] == "t2"
        r.c.post(f"/c/{r.cid}/topic", data={"id": "t1"})  # 머리 누름(보기)
        (fake_cli[1] / "understand.json").write_text(understand_raw([("product", "ZPRD-010")]), encoding="utf-8")
        row3, _, _ = r.turn("가짜기기제품")
        c = row3["consult"]
        assert c["active_topic"] == "t2" and c["topic"]["move"] == "deepen"  # 누른 t1 이 아니라 대화 주제 t2
        procs = row3["items"]["turn_conditions"]["procedure"]
        assert [(v["code"], v.get("from")) for v in procs] == [("ZPRC-010", "product")]  # 제품 → 그 시술로도 칸
    finally:
        r.close()
