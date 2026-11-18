"""WP-7 — 예시 셋 · 끈 상태 대조(S5-T15 · S5-T16 — SPEC-005 §8 을 코디 W-1 ~ W-3 · 검수 W-1 · W-2 · W-4 로 고친 기준).

예시는 SPEC §8 의 **꼴**을 가짜 코드로 옮긴 것이다(수 · 이름은 가짜 — 부록 B 의 실물 수는 E5-1 이 허브로 맞춘다):
① 제품으로 들어옴 → 고민 빈 칸(BK2) → 글자 맞춤 0 인 답이 걸린 선택지로 → 리스트 ② · 관심 표지 · 시너지(안 · 밖) → 계열을 물음
② 같은 이름 고민 · 부위(W-2 — 부위는 짝 고민 코드) → BK1 → 계열 → 계열 묶음 머리에 관심 표지(W-3) → `focus` 가 옮으면 거르개 없음
③ 제품 + 우연 일치(`named` — 객체에 없음 · W-1) → BK2 하나 → 선택지를 이름으로 답함(검수 W-4) → 시너지 → 새 대상
"""

from __future__ import annotations

from tests.conftest import read_log
from tests.state_fixture import Run
from tests.v04_fixture import set_out

# 0.4(WORK-017 §15-4): 입력 = 가짜 이해 출력(가짜 Jev 대신) · 기대 객체는 같은 뜻 · S5-T15(끈 상태 0.1)는 지웠다(S8-T19 가 대신)


def adds(row: dict) -> list[tuple]:
    return [(d["slot"], d["code"], d["to"]) for d in row["consult"]["diff"] if d["op"] == "add"]


def test_example_1_product_entry(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli)
    route = fake_cli[1]
    set_out(route, [("product", "ZZR-1")], kinds=["overview"])
    row1, k1, _ = r.turn("가짜제품 받고 싶어요")
    assert adds(row1) == [("subjects", "ZZR-1", "said")]
    a = row1["consult"]["after"]
    assert [p["code"] for p in a["procedures"]] == ["ZZP-1"] and a["concerns"] == []
    assert row1["consult"]["ask"]["slot"] == "concern" and row1["list"]["concerns"] == [] and "list" in k1
    set_out(route, [("concern", "ZZC-1")], kinds=["indication"])  # 걸린 선택지의 답(규칙 9)
    row2, k2, _ = r.turn("요즘 그게 좀 그래요")
    assert adds(row2) == [("concerns", "ZZC-1", "said")] and row2["consult"]["after"]["focus"] == "ZZC-1"
    proc = row2["list"]["concerns"][0]["modalities"][0]["procedures"][0]
    assert proc["procedure_code"] == "ZZP-1" and proc["interest"] is True
    syn = row2["list"]["synergy"][0]
    assert (len(syn["inside"]), syn["outside_n"]) == (2, 1) and row2["consult"]["ask"] is None
    set_out(route, [("modality", "ZZM-1")], kinds=["overview"])
    row3, _, _ = r.turn("가짜계열 은 뭐예요")
    assert ("subjects", "ZZM-1", "said") in adds(row3)
    assert [p["code"] for p in row3["consult"]["after"]["procedures"]] == ["ZZP-1"]  # 계열은 시술을 정하지 않는다
    assert row3["consult"]["ask"] is None
    r.close()


def test_example_2_concern_entry_same_name_site(tmp_path, fake_cli):
    # 실물 「팔자주름」 꼴(검수 W-2 — 고민 · 부위 둘 다 `target` · 겹침을 가르지 않음)을 가짜 종류 값으로 흉내
    r = Run(tmp_path, fake_cli)
    route = fake_cli[1]
    set_out(route, [("concern", "ZZC-9"), ("site", "ZZS-9")], sites=["ZZS-9"], kinds=["mechanism", "indication"])
    row1, _, _ = r.turn("가짜팔자 가 고민이에요")
    a = row1["consult"]["after"]
    assert ("concerns", "ZZC-9", "said") in adds(row1) and ("sites", "ZZS-9", "said") in adds(row1)  # W-2 — 둘 다
    assert a["sites"][0]["source"]["pair_concern"] == "ZZC-9" and a["focus"] == "ZZC-9"
    q1 = [q for q in row1["consult"]["queries"] if q["rule"] == "QY5"]
    assert q1 and q1[0]["request"] == {"concern_code": "ZZC-9", "site": "가짜팔자"} and q1[0]["skipped"] == "walk"
    assert row1["consult"]["ask"]["slot"] == "interest"  # BK1
    set_out(route, [("modality", "ZZM-1")], kinds=["overview"])
    row2, _, _ = r.turn("가짜계열 은 어떤 거예요")
    assert row2["consult"]["after"]["asked"][0]["answered"] is True  # B-9 — 관심이 생김
    mod = row2["list"]["concerns"][0]["modalities"][0]
    assert mod["interest"] is True and not mod["procedures"][0]["interest"]  # W-3 — 머리에만
    set_out(route, [("concern", "ZZC-9", False), ("concern", "ZZC-1")], kinds=["indication"])
    row3, _, _ = r.turn("가짜팔자 보다는 가짜고민 이 더 고민이에요")
    a3 = row3["consult"]["after"]
    assert a3["focus"] == "ZZC-1" and {x["code"]: x["state"] for x in a3["concerns"]}["ZZC-9"] != "withdrawn"
    assert [q["request"] for q in row3["consult"]["queries"] if q["rule"] in ("QY1", "QY5")] == [{"concern_code": "ZZC-1"}]
    assert row3["list"]["concerns"][0]["concern_code"] == "ZZC-1"
    assert row3["list"]["concerns"][0]["modalities"][0]["interest"] is True
    r.close()


def test_example_3_product_with_coincidental_name(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli)
    route = fake_cli[1]
    set_out(route, [("product", "ZZR-1")], kinds=["overview"])  # 우연 일치 부위는 이해 호출이 고르지 않는다(규칙 5)
    row1, _, _ = r.turn("가짜제품 맞아 가짜부위 까 하는데요")
    a = row1["consult"]["after"]
    assert a["sites"] == [] and adds(row1) == [("subjects", "ZZR-1", "said")]  # W-1 — named 는 객체에 없다
    assert [b["bk"] for b in row1["consult"]["blanks"]] == ["BK2"]  # BK5 없음
    set_out(route, [("concern", "ZZC-1")], kinds=["indication"])
    row2, _, _ = r.turn("가짜고민 때문에요")
    c = row2["consult"]["after"]["concerns"][0]
    assert (c["code"], c["source"]["how"], c["source"]["from_ask"]) == ("ZZC-1", "option", 1)  # OU3 — 앞 선택지면 option
    assert any(q["rule"] == "QY4" for q in row2["consult"]["queries"]) and row2["consult"]["ask"] is None
    set_out(route, [("procedure", "ZZP-1"), ("product", "ZZR-1")], kinds=["same_day"])
    row3, _, _ = r.turn("가짜시술 이랑 같이 받아도 되나요?")
    assert ("subjects", "ZZP-1", "said") in adds(row3) and row3["consult"]["ask"] is None
    assert row3["consult"]["ask_why"] in ("none", "NQ9", "NQ4")
    r.close()


def test_on_state_system_text_and_conv_keeps_state(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli)
    set_out(fake_cli[1], [("concern", "ZZC-1")], kinds=["indication"])
    r.turn("가짜고민 시술")
    cli = [x for x in read_log(r.log) if "-p" in x["argv"] and "--session-id" in x["argv"]]  # 답 실행기(이해 호출 빼고)
    sys_text = cli[0]["argv"][cli[0]["argv"].index("--system-prompt") + 1]
    assert "## 상담 상태" in sys_text and "## 시너지" in sys_text
    r.app.state.ctx.settings.chat_state = "off"  # 서버 값이 바뀌어도 대화는 첫 턴 값으로 끝까지
    row2, _, _ = r.turn("가짜고민 더요")
    assert "consult" in row2 and r.rows("start")[1]["consult_state"] == "on"
    r.close()
