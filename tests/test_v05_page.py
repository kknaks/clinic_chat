"""SPEC-010 WP-2 — 화면 틀(§4 · §12): 시안 마크업 자리(클래스 · data 속성 · 시안 글자) · 빈 slot · 진료항목 접기 · 얼굴 점 ·
몸 목록 · 빈 목록 · 장바구니 · 모달 빈 틀 · 가짜 턴 `items` 이벤트 · FS4 부위 거름 · RT1 코드 전달. 외부 호출 0(고정본 `$J`)."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from clinic_chat.config import ROOT
from clinic_chat.consult import items_view as iv
from clinic_chat.consult import kos_map as km
from clinic_chat.consult.fake_turn import make
from clinic_chat.consult.render import Render, ui_text
from clinic_chat.consult.server import create_app
from tests.conftest import KOS_FIXTURE, KOS_FIXTURE_ITEMS, make_settings

REAL = ROOT / "data" / "kos" / "myeongdong-mapping-20261120-v3.json"
# 시안 정본(앱 밖 · 오케스트레이션 작업 폴더) — 없으면 그 시험만 건너뜀(WP-2 검수 P2-7)
DESIGN = (
    Path.home()
    / "orca/workspaces/kknaks_profile/상담봇/orchestration/work/phub-consult-demo/design-consult-20261120.html"
)
RT1_REAL = ("WHITENING IV DRIP + VITAMIN IV DRIP", "26-시트", "26-", "26--", "26---", "26----")
# §12 「시안에 있는 글자」 + 사용자 문구 둘(D-15) — 시안 design-consult-20261120.html 그대로
DESIGN_TEXT = {
    "+ 새 대화",
    "감지된 시술",
    "종",
    "· 진료항목",
    "개",
    "시술설명",
    "담기",
    "담김",
    "상담",
    "명동점",
    "담은 시술",
    "명동점 상담",
    "시술 목록",
    "최저가 추천",
    "담은 시술 조합 기준",
    "담은 시술이 없어요",
    "모두 비우기",
    "저장",
    "저장했어요",
    "닫기",
    "시술 개요",
    "설명",
    "효과",
    "부작용",
    "진료항목 코드",
    "시술",
    "부위",
    "약물",
    "기기",
    "게시여부",
    "게시됨",
    "시술 원리",
    "시술 방법",
    "빼기",
    "시술 설명 목차",
}
USER_TEXT = {"옵션 추가 +", "추가"}


def kos() -> km.KosMap:
    return km.load(KOS_FIXTURE, expect_items=KOS_FIXTURE_ITEMS)


def app(tmp_path, **kw):
    s = make_settings(tmp_path, chat_dir=str(tmp_path / "chat"), chat_fake_turn=True, **kw)
    return create_app(s, turn_fn=make(0))


def turn(c: TestClient, cid: str, text: str) -> str:
    c.post(f"/c/{cid}/turns", data={"text": text})
    asyncio.run(asyncio.sleep(0))
    return c.get(f"/c/{cid}/turns/1/events").text


# ---------- 시안 글자 · 자리 ----------


@pytest.mark.skipif(not DESIGN.is_file(), reason="시안 파일 없음")
def test_design_text_is_in_design_file():
    """P2-7 — 손으로 적은 시안 글자 목록이 실제 시안 파일에 있다 · 사용자 문구 「옵션 추가 +」 는 시안에 없다."""
    html = DESIGN.read_text(encoding="utf-8")
    assert [v for v in DESIGN_TEXT if v not in html] == []
    assert "옵션 추가 +" not in html


def test_ui_text_is_design_text_only():
    t = ui_text()
    assert set(t.values()) == DESIGN_TEXT | USER_TEXT
    assert not any(k in dir(dict) for k in t)  # Jinja `txt.clear` 이 dict 메서드로 풀리던 함정


def test_page_has_design_frame(tmp_path):
    full = TestClient(app(tmp_path)).get("/").text
    for need in (
        '<div class="cs-right">',
        'class="ms-card cs-top"',
        'class="cs-top__h">상담<',
        'class="cs-top__sub">명동점<',
        "data-cart-toggle",
        'aria-controls="cs-cart"',
        'class="cs-top__n" data-cart-n2>0<',
        '<dialog id="cs-cart" class="ms-modal cs-pm cs-cart"',
        '<dialog id="cs-proc-modal" class="ms-modal cs-pm"',
        'data-slot="drawer-new-label">+ 새 대화<',
        'id="procedure-list" class="cs-list"',
        'id="face-marks"',
    ):
        assert need in full, need
    for v in DESIGN_TEXT - {"빼기", "종", "개", "· 진료항목", "감지된 시술", "시술설명", "담김", "저장했어요"}:
        assert f">{v}<" in full or f'"{v}"' in full or f">{v} <" in full, v
    tabs = re.findall(r'<a href="#pm-(\w+)" class="cs-pm__tab[^"]*">([^<]+)</a>', full)
    assert tabs == [("overview", "시술 개요"), ("desc", "설명"), ("effect", "효과"), ("side", "부작용")]
    for k in ("kind", "name", "lead", "code", "kind2", "site", "drug", "device", "mecText", "metText"):
        assert f'data-pm="{k}"' in full, k
    assert 'data-pm-list="effect"' in full and 'data-pm-list="side"' in full and "<img" not in full  # 그림 = WP-4
    assert full.count('class="cs-rec__card"') == 2 and full.count("cs-sk--short") == 2  # CT4 스켈레톤(CHAT_REC_MAX)
    assert 'data-cart-save data-saved="저장했어요" data-save="저장" disabled' in full  # 빈 장바구니 = 저장 막힘(WP-5)


def test_rec_max_setting(tmp_path):
    full = TestClient(app(tmp_path, chat_rec_max=3)).get("/").text
    assert full.count('class="cs-rec__card"') == 3


def test_empty_list_and_slots(tmp_path):
    full = TestClient(app(tmp_path)).get("/").text
    assert 'data-slot="items-empty"' in full and "cs-kinds" not in full and "<circle" not in full
    for s in (
        "face-title",
        "list-title",
        "chat-title",
        "drawer-list-title",
        "drawer-toggle-label",
        "input-placeholder",
        "send-label",
        "pm-empty",
        "pm-effect-course",
    ):
        assert f'data-slot="{s}"' in full, s
    assert 'data-slot="body-sites-title"' not in full  # 몸 부위가 없으면 머리 없음


# ---------- 진료항목 접기 · 카드 · 점 · 몸 목록(고정본) ----------


def test_view_order_cards_dots_body():
    m = kos()
    v = iv.view(
        m,
        ["ZZ--", "ZZ-LIFT", "ZZ-BTX-B", "ZZ-BTX-A", "ZZ-없음"],
        sites=["ZS-007", "ZS-072", "ZS-057", "ZS-079", "ZS-001", "ZS-072"],
    )  # 테스트 고침 A — 점 = 대화 부위
    assert [k["name"] for k in v["kinds"]] == ["가짜 제모", "가짜 리프팅", "가짜 보톡스"]  # IL2 감지 순
    assert [c["code"] for c in v["kinds"][2]["cards"]] == ["ZZ-BTX-A", "ZZ-BTX-B"]  # 묶음 안 = groups 순서
    assert (v["n_kinds"], v["n_items"]) == (3, 4)
    a = v["kinds"][2]["cards"][0]
    assert (a["site"], a["drug"], a["device"], a["status"]) == ("턱", "가짜톡스", "해당 없음", "")  # IL4 · IL6
    b = v["kinds"][2]["cards"][1]
    assert b["status_slot"] == "status-product-check" and b["status"] == "매핑됨(제품 확인 필요)"
    lift = v["kinds"][1]["cards"][0]
    assert lift["drug"] == "가짜기기" and lift["site"] == "얼굴"  # 약물 해당 없음 → 기기(U-23)
    assert [d["site_code"] for d in v["dots"]] == ["ZS-007", "ZS-072", "ZS-057"]  # 대화 부위 · 같은 부위 한 번
    assert v["dots"][0]["points"] == [[295.0, 318.0]] and len(v["dots"][2]["points"]) == 2  # 비율 × 590 · 1060 · pair
    assert [b["name"] for b in v["body"]] == ["목", "전신"]  # 대화에 나온 순
    assert a["points"] == "295.0,636.0" and lift["body_sites"] == "ZS-079,ZS-001"  # A-5 카드 강조 = 속한 점(전신 ⊃ 목)


def test_drug_chip_hidden_when_both_none():
    it = {"kos_item_code": "X", "kos_medicine": "해당 없음", "kos_machine": "해당 없음"}
    assert iv.drug_chip(it) == ""


def test_items_fragment_markup():
    r = Render(rec_max=2)
    m = kos()
    out = r.items(iv.view(m, ["ZZ-BTX-A", "ZZ-BTX-B", "ZZ-LIFT", "ZZ--"], sites=["ZS-072", "ZS-079"]), "cid1")
    assert out.startswith('<div id="procedure-list" class="cs-list">')
    assert '<p class="cs-kinds__sum">감지된 시술 <b>3종</b> · 진료항목 <b>4개</b></p>' in out
    assert out.count("<details") == 3 and out.count(" open>") == 1  # IL3 첫 묶음만 펼침
    assert (
        '<article class="cs-pcard" aria-pressed="false" data-code="ZZ-BTX-A" data-kind="가짜 보톡스" data-site="턱" '
        'data-device="해당 없음"'
    ) in out
    assert 'data-status="매핑됨(제품 확인 필요)"' in out and 'data-slot="status-product-check"' in out
    assert (
        ">시술설명</button>" in out and 'data-cs-cart aria-pressed="false" data-on="담김" data-off="담기">담기<' in out
    )
    assert 'id="face-marks" class="cs-marks" hx-swap-oob="true"' in out  # 같은 조각 안의 점 · 몸 목록
    assert 'cx="295.0" cy="636.0" r="11"' in out and 'hx-get="/c/cid1/items?site=ZS-072"' in out
    assert 'data-slot="body-sites-title"' in out and 'data-region="body"' in out and ">목</span>" in out


def test_items_escape():
    r = Render()
    v = {
        "kinds": [
            {
                "name": "<zk>",
                "cards": [
                    {**iv.card({"kos_item_code": "<zc>", "kos_item_name": "<zn>"}), "points": "", "body_sites": ""}
                ],
            }
        ],
        "n_kinds": 1,
        "n_items": 1,
        "dots": [],
        "body": [{"site_code": "S", "name": "<zb>"}],
        "site": "",
    }
    out = r.items(v, "c")
    assert not any(x in out for x in ("<zk>", "<zn>", "<zc>", "<zb>")) and "&lt;zb&gt;" in out


# ---------- 가짜 턴 · 장부 · 다시 읽기 ----------


def test_fake_turn_items_event_and_reload(tmp_path):
    a = app(tmp_path)
    c = TestClient(a)
    body = turn(c, "wp2conv000000001", "q")
    kinds = re.findall(r"^event: (\S+)$", body, re.M)
    assert "items" in kinds and "list" not in kinds and "sites" not in kinds
    assert kinds.index("items") < kinds.index("delta")
    row = [r for r in a.state.ctx.ledger.rows("wp2conv000000001") if r["phase"] == "end"][0]
    assert row["items"] == {"codes": ["ZZ-BTX-A", "ZZ-BTX-B", "ZZ-LIFT", "ZZ IV DRIP + VIT"]}  # 앞 묶음 셋
    assert row["list"] is not None  # 0.4 칸은 장부에 남음(ST2)
    page = c.get("/c/wp2conv000000001").text
    assert page.count('class="cs-pcard"') == 4 and "감지된 시술 <b>3종</b>" in page


def test_fake_turn_name_hit_and_empty(tmp_path):
    a = app(tmp_path)
    c = TestClient(a)
    turn(c, "wp2conv000000002", "가짜 제모 궁금")
    assert c.get("/c/wp2conv000000002").text.count('class="cs-pcard"') == 1
    turn(c, "wp2conv000000003", "empty")
    assert 'data-slot="items-empty"' in c.get("/c/wp2conv000000003").text


# ---------- FS4 부위 거름 ----------


def test_site_filter_route(tmp_path):
    c = TestClient(app(tmp_path))
    turn(c, "wp2conv000000004", "q")
    face = c.get("/c/wp2conv000000004/items", params={"site": "ZS-007"}).text  # A-3 — 매핑 부위가 얼굴을 품는 것만
    assert face.count('class="cs-pcard"') == 1  # 목록 4 가운데 리프팅(얼굴 · 전신)만 — 턱 · 입 보톡스 · 수액 빠짐
    jaw = c.get("/c/wp2conv000000004/items", params={"site": "ZS-072"}).text
    assert jaw.count('class="cs-pcard"') == 2 and 'data-site-filter="ZS-072"' in jaw  # 턱 보톡스 + 리프팅(얼굴 ⊃ 턱)
    assert 'class="cs-dot"' not in jaw  # 주제 없는 가짜 턴 = 대화 부위 0 → 점 0(점 누름 꼴은 test_v05_fix_a)
    assert c.get("/c/wp2conv000000004/items").text.count('class="cs-pcard"') == 4
    assert c.get("/c/wp2conv000000004/items", params={"site": "ZS-404"}).status_code == 404


# ---------- 장바구니 줄 · RT1 ----------


def test_cart_item_route_rt1(tmp_path):
    c = TestClient(app(tmp_path))
    for code in ("ZZ IV DRIP + VIT", "ZZ-시트", "ZZ--"):
        r = c.get(f"/c/x/cart/item?code={quote(code, safe='')}")
        assert r.status_code == 200 and f'data-cart-code="{code}"' in r.text, code
    assert c.get("/c/x/cart/item?code=ZZ+IV+DRIP+%2B+VIT").status_code == 200  # 쿼리 + = 공백 · %2B = +
    assert c.get("/c/x/cart/item?code=ZZ+IV+DRIP+++VIT").status_code == 404  # 날 + 는 공백 → 없음(RT1 함정)
    assert c.get("/c/x/cart/item?code=ZZ-NONE").status_code == 404
    assert c.get("/c/x/cart/item").status_code == 422


def test_cart_item_options(tmp_path):
    c = TestClient(app(tmp_path))
    b = c.get("/c/x/cart/item", params={"code": "ZZ-BTX-B"}).text
    assert 'value="P1|P1-3"' in b and "P6-1" not in b  # 판매 종료 상품 뺌(OP3)
    assert ">옵션 추가 +</button>" in b and ">추가</button>" in b and "data-opt-pick hidden" in b
    assert 'aria-label="가짜 입꼬리 보톡스 B 빼기"' in b and ">시술설명</button>" in b
    lift = c.get("/c/x/cart/item", params={"code": "ZZ-LIFT"}).text
    assert 'value="P2|P2-1"' in lift and "P3-1" not in lift and "5000 3000" in lift  # single 만 · 정가 · 이벤트가
    for code in ("ZZ IV DRIP + VIT", "ZZ--"):  # 옵션 0 · group 만 → 단추 없음(OP5)
        assert "data-opt-open" not in c.get("/c/x/cart/item", params={"code": code}).text


@pytest.mark.skipif(not REAL.is_file(), reason="실물 $J(git 무시) 없음")
def test_real_rt1_codes_and_view(tmp_path):
    c = TestClient(app(tmp_path, chat_kos_map=str(REAL), chat_kos_map_items=324))
    for code in RT1_REAL:
        assert c.get("/c/x/cart/item", params={"code": code}).status_code == 200, code
    m = km.load(REAL)
    v = iv.view(m, [i["kos_item_code"] for i in m.items], sites=list(m.sites))
    assert v["n_items"] == 324 and v["n_kinds"] == 131
    face = [s for s in m.sites.values() if s["region"] == "face"]
    assert len(face) == 53 and all(s["shape"] in ("point", "pair") and s["points"] for s in face)  # A-7 얼굴 53 전부 점
    assert m.sites["SITE-046"]["name"] == "팔자주름" and len(v["dots"]) == 53
    assert all(0 <= x <= 590 and 0 <= y <= 1060 for d in v["dots"] for x, y in d["points"])


def test_ledger_has_no_prices(tmp_path):
    a = app(tmp_path)
    c = TestClient(a)
    turn(c, "wp2conv000000005", "q")
    c.get("/c/wp2conv000000005/cart/item", params={"code": "ZZ-BTX-A"})
    raw = (tmp_path / "chat" / "turns.jsonl").read_text(encoding="utf-8")
    assert "price_amount" not in raw and "1000" not in json.dumps(
        [r.get("items") for r in map(json.loads, raw.splitlines())]
    )


# ---------- WP-2 추가분 — IL11 주제 묶음 틀 ----------

TOPICS = [
    {
        "id": "t1",
        "conditions": {
            "site": [{"code": "ZS-072", "name": "턱"}],
            "procedure": [{"code": "ZPRC-001", "name": "보톡스"}],
        },
        "items": ["ZZ-BTX-A"],
        "created_turn": 1,
    },
    {
        "id": "t2",
        "conditions": {"concern": [{"code": "C", "name": "주름"}], "country": "KR"},
        "items": ["ZZ-LIFT", "ZZ--"],
        "sites": ["ZS-007", "ZS-001", "ZS-079"],  # 테스트 고침 A — 그 주제의 대화 부위
        "created_turn": 2,
    },
    {
        "id": "t3",
        "conditions": {"product": [{"code": "P", "name": "가짜기기"}]},
        "items": ["ZZ-LIFT"],
        "created_turn": 3,
    },
]


def test_cond_names_order():
    c = {
        "country": "KR",
        "product": [{"name": "p"}],
        "effect": [{"name": "e"}],
        "concern": [{"name": "c"}],
        "site": [{"name": "s"}],
        "procedure": [{"name": "x"}, {"code": "ZPRC-9"}],
    }
    assert iv.cond_names(c) == ["s", "c", "x", "ZPRC-9", "e", "p", "KR"]  # 테스트 고침 E — 부위 → 고민 → 시술 → 효과 → 제품 → 국가
    c["procedure"].append({"code": "P2", "name": "제품풀이", "from": "product"})
    c["product"].append({"name": "x"})
    assert "제품풀이" not in iv.cond_names(c) and iv.cond_names(c).count("x") == 1  # 제품 풀이 시술 0 · 같은 이름 한 번


def test_topics_view_order_and_max():
    m = kos()
    v = iv.topics_view(m, TOPICS, "t2")
    assert [(t["id"], t["current"], t["n"]) for t in v["topics"]] == [
        ("t2", True, 2),
        ("t3", False, 1),
        ("t1", False, 1),
    ]
    assert v["show_topics"] and v["n_items"] == 2  # 안 = 지금 주제 진료항목
    assert [d["site_code"] for d in v["dots"]] == ["ZS-007"] and [b["name"] for b in v["body"]] == ["전신", "목"]  # TP9
    assert [t["id"] for t in iv.topics_view(m, TOPICS, "t2", max_n=2)["topics"]] == ["t2", "t3"]  # TP11
    assert iv.topics_view(m, TOPICS, None)["topics"][0]["id"] == "t3"  # 지금 없음 → 마지막
    one = iv.topics_view(m, TOPICS[:1], "t1")
    assert one["show_topics"]  # 테스트 고침 E — 주제가 하나여도 머리


def test_topics_markup():
    r = Render()
    out = r.items(iv.topics_view(kos(), TOPICS, "t2"), "cid9")
    assert out.count('<details class="cs-topic"') == 3 and out.count("data-topic-current open") == 1
    cur = out.split('data-topic-id="t3"')[0]
    assert '<span class="cs-topic__mark" data-slot="topic-current-mark"></span>' in cur
    assert (
        '<span class="cs-topic__name"><span class="cs-topic__cond">주름</span><span class="cs-topic__cond">KR</span></span>'
        in cur
    )
    assert '<span class="cs-topic__n">2</span>' in cur and 'class="cs-kinds"' in cur  # 안 = 시안 cs-kinds 그대로
    assert out.count('data-slot="topic-prev-title"') == 1
    prev = out.split('data-topic-id="t3"')[1]
    assert "data-topic-switch" in prev and 'hx-post="/c/cid9/topic"' in prev and """hx-vals='{"id": "t1"}'""" in prev
    assert "cs-kinds" not in prev and "cs-pcard" not in prev  # 앞 주제 = 머리만
    assert ">턱<" in prev and ">보톡스<" in prev and prev.index(">턱<") < prev.index(">보톡스<")  # 부위 → 시술(고침 E)
    one = r.items(iv.topics_view(kos(), TOPICS[:1], "t1"), "c")
    assert one.count('<details class="cs-topic"') == 1 and 'class="cs-kinds"' in one  # 테스트 고침 E — 하나여도 머리


def test_fake_turn_two_topics_and_switch_route(tmp_path):
    a = app(tmp_path)
    c = TestClient(a)
    cid = "wp2conv000000010"
    turn(c, cid, "가짜 제모")
    c.post(f"/c/{cid}/turns", data={"text": "가짜 리프팅"})
    asyncio.run(asyncio.sleep(0))
    body = c.get(f"/c/{cid}/turns/2/events").text
    assert "cs-topic" in body and "topic-prev-title" in body
    page = c.get(f"/c/{cid}").text
    assert re.search(r'data-topic-id="t2" data-topic-current open', page) and 'data-topic-id="t1">' in page
    r = c.post(f"/c/{cid}/topic", data={"id": "t1"})
    assert r.status_code == 200 and 'data-topic-id="t1" data-topic-current open' in r.text
    assert 'id="face-marks" class="cs-marks" hx-swap-oob="true"' in r.text
    rows = a.state.ctx.ledger.rows(cid)
    assert rows[-1]["phase"] == "topic_switch" and rows[-1]["topic_id"] == "t1"
    assert 'data-topic-id="t1" data-topic-current open' in c.get(f"/c/{cid}").text  # 새로 고침도 머리 누름 유지
    a2 = app(tmp_path)  # 재시작 — 마지막 맺는 줄 뒤 topic_switch 가 active_topic 을 덮는다(TP10 · r6 P2-3)
    assert 'data-topic-id="t1" data-topic-current open' in TestClient(a2).get(f"/c/{cid}").text
    assert c.post(f"/c/{cid}/topic", data={"id": "t9"}).status_code == 404
    assert c.post("/c/nosuchconv000001/topic", data={"id": "t1"}).status_code == 404


def test_topic_max_setting(tmp_path):
    c = TestClient(app(tmp_path, chat_topic_max=2))
    cid = "wp2conv000000011"
    for n, q in enumerate(("가짜 제모", "가짜 리프팅", "가짜 수액"), 1):
        c.post(f"/c/{cid}/turns", data={"text": q})
        asyncio.run(asyncio.sleep(0))
        c.get(f"/c/{cid}/turns/{n}/events")
    assert c.get(f"/c/{cid}").text.count('<details class="cs-topic"') == 2


# ---------- WP-2 검수 P2 ----------


def test_panel_kos_line(tmp_path):
    s = make_settings(tmp_path, chat_dir=str(tmp_path / "chat"), chat_fake_turn=True, chat_debug_panel="on")
    full = (
        TestClient(create_app(s, turn_fn=make(0), hub_health=lambda: {"build": {"gold_hash": "other"}})).get("/").text
    )
    m = kos()
    assert 'class="cs-state__kos" data-kos-hash-mismatch="True"' in full and f"kos_map {m.sha256[:8]}" in full  # P2-1
    assert (
        "kos_check" not in TestClient(app(tmp_path)).get("/").text
        and "cs-state__kos" not in TestClient(app(tmp_path)).get("/").text
    )


def test_face_check_shows_kos_sites(tmp_path):
    out = TestClient(app(tmp_path)).get("/face-check").text  # P2-2 · FS6
    assert "data-kos-check" in out and out.count("cs-dot--kos") == 1 + 1 + 1 + 2  # 고정본 얼굴 4 부위 · pair 2 점
    assert 'data-site-code="ZS-057"' in out and ">ZS-057<" in out


def test_cart_kind_without_site(tmp_path):
    b = TestClient(app(tmp_path)).get("/c/x/cart/item", params={"code": "ZZ IV DRIP + VIT"}).text  # P2-6
    assert '<span class="cs-cart__kind">가짜 수액</span>' in b
    a = TestClient(app(tmp_path)).get("/c/x/cart/item", params={"code": "ZZ-BTX-A"}).text
    assert '<span class="cs-cart__kind">가짜 보톡스 · 턱</span>' in a
