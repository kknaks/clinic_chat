"""SPEC-010 WP-6 — LLM 블록(§7-1 LC1 ~ LC9) · 답 규칙 재료(R7 ~ R11) · 덧붙이기 · 채팅 원리 그림(§4-4 CH3). 외부 호출 0."""

from __future__ import annotations

import json
import re
from pathlib import Path

from clinic_chat.consult import kos_block as kb
from clinic_chat.consult import kos_map as km
from clinic_chat.consult import topics as tp
from clinic_chat.consult.prompt import load
from clinic_chat.consult.render import Render
from clinic_chat.consult.turn import fig_re
from tests.conftest import KOS_FIXTURE, KOS_FIXTURE_ITEMS, read_log, stream
from tests.state_fixture import TAIL_MARK, Run, state_hub
from tests.v04_fixture import entry_rows, list_route

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "consult.json"
KOS = load(PROMPT).kos
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>'


def kos() -> km.KosMap:
    return km.load(KOS_FIXTURE, expect_items=KOS_FIXTURE_ITEMS)


def fresh():
    return {"codes": set(), "pairs": set(), "cart_sig": None}


def build(**kw):
    base = dict(
        cond={"topic": "t1"},
        items=[],
        cart=[],
        cart_rec=None,
        viewed=[],
        pairs={},
        pairs_capped=False,
        course=[],
        figs=[],
        sent=fresh(),
        items_max=30,
    )
    base.update(kw)
    return kb.build(kos(), KOS, **base)


def lines_of(text, label):
    return [json.loads(x[len(label) + 1 :]) for x in text.splitlines() if x.startswith(label + " ")]


def test_prompt_kos_section():
    assert KOS["block_header"] and "{code}" in KOS["fig_marker"] and len(KOS["rules"]) == 27  # SPEC-012 R17 ~ R21 · 고침 2 R22 ~ R24 · 고침 3 R25
    assert any(r.startswith("R11.") for r in KOS["rules"]) and load(PROMPT).version == "0.6.0-draft"
    r10 = [r for r in KOS["rules"] if r.startswith("R10.")][0]
    assert "장바구니" in r10 and "재시술 간격" in r10  # S-2 — R10 나눔
    assert any("같이 받아도" in r and "talk" in r for r in load(PROMPT).pick["rules"])  # S-2 pick 규칙


def test_lc2_lc3_item_lines_and_overflow():
    text, meta = build(items=["ZZ-BTX-A", "ZZ-BTX-B", "ZZ-LIFT"], items_max=2)
    items = lines_of(text, KOS["labels"]["items"])
    assert [x["code"] for x in items] == ["ZZ-BTX-A", "ZZ-BTX-B"] and meta["codes"] == ["ZZ-BTX-A", "ZZ-BTX-B"]
    a, b = items
    assert (
        a["name"] == "가짜 턱 보톡스 A"
        and a["status"] == "매핑됨"
        and "note" not in a
        and a["case_codes"] == ["ZCAS-001"]
    )
    assert b["status"] == "매핑됨(제품 확인 필요)" and "note" not in b  # 고침 2 — 데이터 메모는 답 모델에 싣지 않음(R3 는 status 로 돌려 말함)
    assert [o["title"] for o in a["options"]][:2] == ["가짜 턱 보톡스 1회", "가짜 턱 보톡스 5회"]
    grp = [o for o in a["options"] if o["type"] == "group"][0]
    assert (
        grp["covers"] == ["가짜 턱 보톡스 A", "가짜 리프팅"] and a["options"][0]["price_amount"] == 1000
    )  # 가격 = JSON 값
    assert f"{KOS['overflow']} 1" in text and meta["overflow"] == 1  # LC1 그 밖 n


def test_lc7_conditions_line_topic_blocked_country_ask():
    dec = tp.Decision([{"id": "t2", "conditions": {}, "items": []}], "t2", {"change": True, "by": "marker"})
    ir = {
        "codes": ["ZZ-BTX-A"],
        "conditions": {"concern": [{"table": "concern", "code": "C", "name": "기미"}], "country": "KR"},
        "turn_conditions": {"concern": [{"code": "C"}]},
        "blocked": {"slot": "product"},
        "country_dropped": 3,
    }
    cond = kb.cond_view(
        dec, ir, {"axis": "site", "options": [{"name": "턱"}]}, [{"id": "t1", "conds": ["필러"], "n": 6}]
    )
    assert cond["topic_change"] == {"to": "t2", "by": "marker", "conditions": ["기미", "KR"]}  # R11
    assert cond["blocked"] == {"slot": "product"} and cond["country_dropped"] == 3  # R8 · R7
    assert cond["ask"] == {"axis": "site", "options": ["턱"]} and cond["changed"] == ["concern"]  # R9
    assert cond["prev_topics"] == [{"id": "t1", "conds": ["필러"], "n": 6}] and cond["country"] == "KR"
    assert cond["list_n"] == 1 and cond["list_codes"] == ["ZZ-BTX-A"]  # LC5 로 줄을 안 보내도 지금 목록은 앎
    text, _ = build(cond=cond)
    assert lines_of(text, KOS["labels"]["conditions"])[0]["topic_change"]["by"] == "marker"


def test_lc8_cart_and_rec_lines():
    m = kos()
    from clinic_chat.consult import cart as cart_mod

    cart = [{"code": "ZZ-LIFT", "lines": [["P2", "P2-1"]]}, {"code": "ZZ--", "lines": []}]
    rec = cart_mod.rec_view(m, cart)
    text, meta = build(cart=cart, cart_rec=rec)
    c = lines_of(text, KOS["labels"]["cart"])[0]
    assert c[0]["lines"] == [
        {
            "product": "[ZZ 이벤트] 가짜 리프팅",
            "title": "가짜 리프팅 1회",
            "count": 1,
            "price_amount": 5000,
            "event_price": 3000,
        }
    ]
    r = lines_of(text, KOS["labels"]["rec"])[0]
    assert r["cards"][0]["total"] == 3000 and r["cards"][0]["list_total"] == 5000 and r["no_price"] == ["가짜 목 제모"]
    assert [x["code"] for x in lines_of(text, KOS["labels"]["items"])] == ["ZZ-LIFT", "ZZ--"]  # 장바구니 진료항목도
    assert meta["cart_sig"]


def test_lc5_append_only():
    cart = [{"code": "ZZ-LIFT", "lines": []}]
    t1, m1 = build(items=["ZZ-BTX-A"], cart=cart, pairs={("ZPRC-001", "ZPRC-010"): []})
    sent = kb.sent_before([{"llm": {"answered": True, "kos_block": m1}}])
    t2, m2 = build(items=["ZZ-BTX-A", "ZZ-BTX-B"], cart=cart, pairs={("ZPRC-001", "ZPRC-010"): []}, sent=sent)
    assert m2["codes"] == ["ZZ-BTX-B"] and m2["pairs"] == []  # 앞 턴에 보낸 진료항목 · 쌍 다시 0
    assert not lines_of(t2, KOS["labels"]["cart"]) and lines_of(t1, KOS["labels"]["cart"])  # 장바구니 그대로면 0
    t3, _ = build(cart=cart + [{"code": "ZZ--", "lines": []}], sent=sent)
    assert lines_of(t3, KOS["labels"]["cart"])  # 바뀐 턴만


def test_lc5_exception_together_resends_cart_and_pairs():
    cart = [{"code": "ZZ-BTX-A", "lines": []}, {"code": "ZZ-BTX-B", "lines": []}]
    _, m1 = build(cart=cart, pairs={("ZPRC-001", "ZPRC-001"): [{"question_class": "재시술 간격"}]})
    sent = kb.sent_before([{"llm": {"answered": True, "kos_block": m1}}])
    t2, m2 = build(cart=cart, pairs={("ZPRC-001", "ZPRC-001"): [{"question_class": "재시술 간격"}]}, sent=sent)
    assert not lines_of(t2, KOS["labels"]["pairs"]) and not lines_of(t2, KOS["labels"]["cart"])  # 보통 턴 = 다시 0
    t3, m3 = build(
        cart=cart, pairs={("ZPRC-001", "ZPRC-001"): [{"question_class": "재시술 간격"}]}, sent=sent, force_cart=True
    )
    assert lines_of(t3, KOS["labels"]["pairs"])[0]["rows"][0]["question_class"] == "재시술 간격"
    assert lines_of(t3, KOS["labels"]["cart"]) and m3["force_cart"] is True
    assert any(w in "같이 받아도 돼요?" for w in kb.TOGETHER)


def test_p2_1_failed_turn_block_is_not_sent():
    _, m1 = build(items=["ZZ-BTX-A"])
    assert kb.sent_before([{"llm": {"answered": False, "kos_block": m1}}])["codes"] == set()
    assert kb.sent_before([{"llm": {"answered": True, "kos_block": m1}}])["codes"] == {"ZZ-BTX-A"}


def test_lc9_pairs_same_procedure_and_rows():
    m = kos()
    cart = [{"code": "ZZ-BTX-A"}, {"code": "ZZ-BTX-B"}, {"code": "ZZ-LIFT"}]
    assert kb.cart_pairs(m, cart) == [("ZPRC-001", "ZPRC-001"), ("ZPRC-001", "ZPRC-010")]  # 같은 시술 쌍(a = b)도
    rows = [{"question_class": "간격", "same_day": "조건부", "text": "원문", "gid": "G1", "a_layer": {}}]
    text, meta = build(pairs={("ZPRC-001", "ZPRC-010"): rows})
    p = lines_of(text, KOS["labels"]["pairs"])[0]
    assert p["a"] == "ZPRC-001" and p["rows"][0]["question_class"] == "간격" and p["rows"][0]["text"] == "원문"
    assert meta["pairs"] == ["ZPRC-001|ZPRC-010"]


def test_course_and_figs_lines():
    text, meta = build(
        course=[{"code": "ZZ-BTX-A", "course": [{"onset_text": "2일"}], "side_other": []}],
        figs=[{"code": "MEC-001", "name": "아세틸콜린 차단"}],
    )
    assert lines_of(text, KOS["labels"]["course"])[0]["course"][0]["onset_text"] == "2일"
    assert lines_of(text, KOS["labels"]["figs"])[0] == [{"code": "MEC-001", "name": "아세틸콜린 차단"}]
    assert meta["figs"] == ["MEC-001"]


def test_ch3_marker_to_figure_and_delta_hidden():
    r = Render()
    r.fig_rx = fig_re(KOS["fig_marker"])
    body = (
        "앞 글\n\n"
        + KOS["fig_marker"].replace("{code}", "MEC-001")
        + "\n\n뒤 글\n"
        + KOS["fig_marker"].replace("{code}", "MEC-999")
    )
    out = r.answer(1, body, None, {"code": "MEC-001", "name": "아세틸콜린 차단"})
    assert '<figure class="cs-fig" data-fig-code="MEC-001">' in out and ">아세틸콜린 차단</figcaption>" in out
    assert out.index("앞 글") < out.index("cs-fig") < out.index("뒤 글") and "MEC-999" not in out and "[[" not in out
    none = r.answer(1, body, None, None)  # 그림 없음 → 표지 줄 지움
    assert "cs-fig" not in none and "[[" not in none and "뒤 글" in none
    assert "[[" not in r.delta(body) and "뒤 글" in r.delta(body)


# ---------- 한 턴 끝까지(가짜 CLI · 가짜 허브) ----------

EXTRA = [
    {
        "entity_table": "procedure",
        "entity_code": "ZPRC-001",
        "entity_name": "가짜보톡스시술",
        "name_kind": "정식",
        "text": "가짜보톡스시술",
    }
]


def hub_with_modal():
    h = state_hub()
    h.routes["resolve"] = list_route(entry_rows() + EXTRA)
    base = h.routes["facets"]

    def facets(p):
        if p.get("facet") == "profile" and p.get("node") == "procedure_case:ZCAS-001":
            return {
                "rows": [
                    {
                        "subject_table": "procedure",
                        "subject_code": "ZPRC-001",
                        "mechanism_code": "MEC-001",
                        "mechanism_name": "아세틸콜린 차단",
                        "overview": "개요",
                        "layer": {"holder": {"holder_table": "procedure"}},
                    }
                ]
            }
        if p.get("facet") == "outcome" and p.get("node") == "procedure_case:ZCAS-001":
            return {"rows": [{"effect_code": "", "onset_text": "2일부터"}]}
        return base(p)

    h.routes["facets"] = facets
    h.routes["pair"] = lambda p: {
        "rows": [{"question_class": "간격", "same_day": "조건부", "text": "pair 원문"}],
        "same_day_packages": [],
    }
    return h


def understand(targets, kinds=(), kind="new") -> str:
    raw = json.dumps(
        {
            "targets": [{"table": t, "code": c, "sure": True} for t, c in targets],
            "kind": kind,
            "sites": [],
            "withdraw": [],
            "kinds": list(kinds),
            "out": False,
        },
        ensure_ascii=False,
    )
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": raw,
            "duration_api_ms": 9,
            "usage": {"input_tokens": 3, "output_tokens": 2},
            "modelUsage": {"claude-test": {}},
        },
        ensure_ascii=False,
    )


def test_turn_block_pairs_figure_ledger(tmp_path, fake_cli):
    imgs = tmp_path / "images"
    (imgs / "mechanism").mkdir(parents=True)
    (imgs / "mechanism" / "MEC-001.svg").write_bytes(SVG)
    r = Run(tmp_path, fake_cli, hub=hub_with_modal(), chat_image_dir=str(imgs))
    try:
        marker = KOS["fig_marker"].replace("{code}", "MEC-001")
        (fake_cli[1] / "answer.jsonl").write_text(
            stream(f"원리 설명\n{marker}\n끝\n{TAIL_MARK}\n1: 1\n"), encoding="utf-8"
        )
        (fake_cli[1] / "understand.json").write_text(understand([("procedure", "ZPRC-001")]), encoding="utf-8")
        for code in ("ZZ-BTX-A", "ZZ-LIFT"):
            r.c.post(f"/c/{r.cid}/cart", data={"op": "add", "code": code})
        r.c.get(f"/c/{r.cid}/item", params={"code": "ZZ-BTX-A"})  # MD4 — 본 진료항목
        row, kinds, body = r.turn("가짜보톡스시술 원리가 뭐야")
        kbm = row["llm"]["kos_block"]
        assert kbm["lines"] > 3 and kbm["chars"] > 0 and "ZZ-BTX-A" in kbm["codes"]  # LC6
        assert kbm["pairs"] == ["ZPRC-001|ZPRC-010"] and kbm["figs"] == ["MEC-001"]
        stdin = [x["stdin"] for x in read_log(r.log) if "--no-session-persistence" not in x["argv"]][-1]
        assert KOS["block_header"] in stdin and "pair 원문" in stdin and "2일부터" in stdin  # LC9 · 경과(N3)
        assert stdin.index(KOS["block_header"]) < stdin.rindex("가짜보톡스시술 원리가 뭐야")
        assert row["fig"] == {"fig": {"code": "MEC-001", "name": "아세틸콜린 차단"}, "dropped": []}
        assert 'data-fig-code="MEC-001"' in body and "[[" not in body.split("event: done")[1]
        page = r.c.get(f"/c/{r.cid}").text
        assert 'src="/img/mechanism/MEC-001.svg"' in page  # 다시 읽기에도 그림
        row2, _, _ = r.turn("가짜보톡스시술 또")
        assert row2["llm"]["kos_block"]["pairs"] == [] and "ZZ-BTX-A" not in row2["llm"]["kos_block"]["codes"]  # LC5
        (fake_cli[1] / "understand.json").write_text(
            understand([], kinds=["combo"], kind="talk"), encoding="utf-8"
        )
        row3, _, _ = r.turn("같이 받아도 돼요?")  # S-2 — 대상 0 · 장바구니 쌍 줄 다시(SPEC-012 — 1차 kinds combo 로 판정 · 낱말 아님)
        assert row3["llm"]["kos_block"]["pairs"] == ["ZPRC-001|ZPRC-010"] and row3["llm"]["kos_block"]["force_cart"]
        assert row3["consult"]["topic"]["move"] == "same" and not (row3.get("items") or {}).get("blocked")
        # 검수 P2-2 — 같은 낱말 말이라도 1차 kinds 가 비면(같이 받기 물음 아님) 짝 줄을 다시 싣지 않는다 · kinds combo 면 다시
        (fake_cli[1] / "understand.json").write_text(understand([], kinds=[], kind="talk"), encoding="utf-8")
        row4, _, _ = r.turn("같이 받아도 돼요?")
        assert row4["llm"]["kos_block"]["pairs"] == [] and not row4["llm"]["kos_block"]["force_cart"]
        (fake_cli[1] / "understand.json").write_text(understand([], kinds=["same_day"], kind="talk"), encoding="utf-8")
        row5, _, _ = r.turn("오늘 같이 받을 수 있어?")
        assert row5["llm"]["kos_block"]["pairs"] == ["ZPRC-001|ZPRC-010"] and row5["llm"]["kos_block"]["force_cart"]
        # P1-1 (가) — 가격 숫자 0 범위 = `cart` · `rec` · `items` · `consult` 칸(프롬프트 보관본은 블록 사본이라 가격이 듦)
        for rr in r.rows():
            for k in ("items", "consult"):
                blob = json.dumps(rr.get(k), ensure_ascii=False)
                assert not any(x in blob for x in ("price_amount", "event_price", "list_total")), k
                assert not re.search(r":\s*(5000|3000|1000|6000|3500)\b", blob), k
        for rr in r.rows("cart"):
            assert not re.search(r"\b(5000|3000|1000|6000|3500)\b", json.dumps(rr))
    finally:
        r.close()
